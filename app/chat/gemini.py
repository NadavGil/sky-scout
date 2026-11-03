import json

import httpx

from app.config import Settings
from app.http import Sleeper, send

TOOLS = ("screen", "compare", "share", "gap", "brief", "method")
API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
FALLBACK_MODELS = ("gemini-3.5-flash-lite", "gemini-flash-lite-latest")
UNAVAILABLE = {404, 500, 503, 504}


class ModelError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _string(description: str) -> dict:
    return {"type": "string", "description": description}


def function_declarations() -> list[dict]:
    return [
        {
            "name": "screen",
            "description": "Screen one U.S. region for a renovation project type. Returns scoring JSON, not a profitability rank.",
            "parameters": {
                "type": "object",
                "properties": {
                    "region": _string("Census region, division, or state."),
                    "project_type": _string("Project type. Omit when the customer did not name one."),
                },
                "required": ["region"],
            },
        },
        {
            "name": "compare",
            "description": "Compare airports on one metric and one period. Congestion is BTS on-time arrival delay, never a live aircraft count.",
            "parameters": {
                "type": "object",
                "properties": {
                    "airports": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Airport names or codes.",
                    },
                    "metric": _string("Use congestion."),
                    "period": _string("One YYYY-MM for every airport. Omit when each airport has its own period."),
                    "periods": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "One period per airport, in the same order. Use the customer's words, such as last year or this year.",
                    },
                },
                "required": ["airports", "metric"],
            },
        },
        {
            "name": "brief",
            "description": "Summarize the previous scored answer: recommendation, sources, assumptions, and biggest uncertainty. Do not calculate a new score.",
            "parameters": {
                "type": "object",
                "properties": {"word_limit": {"type": "integer", "description": "Maximum words. Use 150 when the customer asks for 150."}},
            },
        },
        {
            "name": "method",
            "description": "State the evidence rule that distinguishes a terminal bottleneck from a runway or airspace bottleneck. Do not score an airport.",
            "parameters": {
                "type": "object",
                "properties": {"topic": _string("Use bottleneck.")},
            },
        },
        {
            "name": "share",
            "description": "Share of one flight category at one airport. Long-haul is at least 3000 statute miles.",
            "parameters": {
                "type": "object",
                "properties": {
                    "airport": _string("Airport name or code."),
                    "category": _string("Use long_haul."),
                    "period": _string("YYYY for a T-100 year, or YYYY-MM-DD for one OpenSky day."),
                },
                "required": ["airport", "category"],
            },
        },
        {
            "name": "gap",
            "description": "Score a missing quantity. Unmet demand is unconstrained demand minus effective capacity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "airport": _string("Airport name or code."),
                    "quantity": _string("Use unmet_demand."),
                },
                "required": ["airport", "quantity"],
            },
        },
    ]


ROUTE_SYSTEM = (
    "Route the latest analyst question to exactly one of screen, compare, share, gap, brief, or method. "
    "Do not calculate. Do not use web search. If the question is outside those families, do not call a tool. "
    "Call brief when the customer asks to summarize the recommendation, sources, assumptions, or uncertainty. "
    "Call method when the customer asks what evidence distinguishes a terminal, runway, or airspace bottleneck. "
    "When a comparison gives each airport its own period, pass periods in the same order as airports and keep phrases such as last year and this year. "
    "Keep a project type, region, airport, metric, or period from earlier turns when the new question does not change it. "
    "Pass the customer's names through. LA means the words LA. Santa Ana means the words Santa Ana."
)
EXPLAIN_RULES = (
    "Explain the scoring JSON in plain sentences and nothing beyond it. Do not calculate a new rank, share, gap, or score. Do not use web search. "
    "State the territory. For a screen, also state the region, its states, the peer set, and the project type, then quote the reason. "
    "If ranked is empty, say there is no rank. "
    "If the status is insufficient_data, watchlist, unavailable, unknown, unresolved, incomplete_year, not_routed, "
    "or model_unavailable, say there is no sound answer for that part. "
    "Do not call an analyst-supplied project cost measured. Do not say a renovation is profitable unless profitability.claimed is true. "
    "End by saying whether profitability is claimed. "
    "Live traffic, weather, runway count, runway length, airport size, and growth are not profitability or capacity."
)


def _contents(turns: list[dict]) -> list[dict]:
    contents = []
    for turn in turns:
        role = "model" if turn.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": turn.get("content") or ""}]})
    return contents


def _generation(max_tokens: int) -> dict:
    return {
        "temperature": 0,
        "maxOutputTokens": max_tokens,
        "thinkingConfig": {"thinkingLevel": "low"},
    }


def route_body(turns: list[dict]) -> dict:
    return {
        "systemInstruction": {"parts": [{"text": ROUTE_SYSTEM}]},
        "contents": _contents(turns),
        "tools": [{"functionDeclarations": function_declarations()}],
        "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
        "generationConfig": _generation(1024),
    }


def explain_body(skill: str, result: dict) -> dict:
    return {
        "systemInstruction": {"parts": [{"text": skill + "\n\n" + EXPLAIN_RULES}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(result)}]}],
        "generationConfig": _generation(4096),
    }


def endpoint(model: str) -> str:
    return f"{API_ROOT}/{model}:generateContent"


def candidate_models(primary: str) -> list[str]:
    models = [primary]
    for name in FALLBACK_MODELS:
        if name not in models:
            models.append(name)
    return models


def parse_route(payload: dict) -> dict | None:
    for part in _parts(payload):
        call = part.get("functionCall")
        if not isinstance(call, dict) or call.get("name") not in TOOLS:
            continue
        arguments = call.get("args") or {}
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        return {"name": call["name"], "arguments": arguments}
    return None


def parse_explanation(payload: dict) -> str:
    texts = [part.get("text", "") for part in _parts(payload) if part.get("text") and not part.get("thought")]
    return "\n".join(part for part in texts if part).strip()


def _parts(payload: dict) -> list[dict]:
    candidates = payload.get("candidates") or []
    if not candidates:
        return []
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    return [part for part in parts if isinstance(part, dict)]


class GeminiModel:
    name = "gemini"

    def __init__(self, http: httpx.AsyncClient, settings: Settings, sleeper: Sleeper):
        self.http = http
        self.settings = settings
        self.sleeper = sleeper
        self.active_model = settings.gemini_model

    async def route(self, turns: list[dict]) -> dict | None:
        payload = await self._post(route_body(turns))
        return parse_route(payload)

    async def explain(self, skill: str, result: dict) -> str:
        payload = await self._post(explain_body(skill, result))
        reply = parse_explanation(payload)
        if not reply:
            raise ModelError("Gemini returned no explanation")
        return reply

    async def _post(self, body: dict) -> dict:
        last_status = None
        for model in candidate_models(self.active_model):
            try:
                response = await send(
                    self.http,
                    "POST",
                    endpoint(model),
                    "gemini",
                    self.sleeper,
                    json=body,
                    headers={"x-goog-api-key": self.settings.gemini_api_key, "content-type": "application/json"},
                    timeout=20,
                )
            except httpx.TimeoutException:
                last_status = "timeout"
                continue
            except httpx.RequestError:
                last_status = "unreachable"
                continue
            if response.status_code == 200:
                self.active_model = model
                try:
                    return response.json()
                except json.JSONDecodeError as exc:
                    raise ModelError("Gemini response was not JSON") from exc
            last_status = response.status_code
            if response.status_code not in UNAVAILABLE:
                break
        if last_status == "timeout":
            raise ModelError("timeout")
        if last_status == "unreachable":
            raise ModelError("unreachable")
        raise ModelError(f"HTTP {last_status}")
