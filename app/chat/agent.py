import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chat.gemini import ModelError
from app.chat.local import LocalModel, _project, follow_up
from app.config import Settings
from app.models import Message
from app.scoring.evidence import extract_evidence, record_analyst_evidence, redact_for_model, redact_result
from app.services.catalog import lookup_airport
from app.scoring.explain import explain_locally
from app.scoring.service import not_routed, run_tool

SKILL_PATH = (
    Path(__file__).resolve().parents[2] / ".cursor" / "skills" / "us-airport-renovation-opportunity-ranking" / "SKILL.md"
)


class Agent:
    def __init__(self, model, settings: Settings):
        self.model = model
        self.settings = settings
        self.skill = SKILL_PATH.read_text()

    async def respond(self, session: Session, conversation_id: int) -> tuple[str, dict]:
        messages = _messages(session, conversation_id)
        if not messages:
            result = not_routed(session, conversation_id, "There is no question to answer.")
            return explain_locally(result), result
        latest = messages[-1].content
        airport_id = _single_airport_id(session, latest)
        for kind, value in extract_evidence(latest):
            scoped = airport_id if kind in {"bottleneck", "intervention", "capacity_mapping"} else None
            record_analyst_evidence(session, conversation_id, kind, value, scoped)
        session.flush()
        allow_costs = self.settings.allow_project_costs_to_model
        turns = [
            {"role": message.role, "content": redact_for_model(message.content, allow_costs)}
            for message in messages
            if message.role in {"user", "assistant"}
        ]
        route = follow_up(latest)
        failure = None
        if route is None:
            try:
                route = await self.model.route(turns)
            except ModelError as exc:
                failure = exc
                if getattr(self.model, "name", "") != LocalModel.name:
                    route = await LocalModel().route(turns)
        if route is None and failure is not None:
            result = not_routed(session, conversation_id, f"The model call failed ({failure.message}). There is no scored answer.")
            return explain_locally(redact_result(result, allow_costs)), result
        if not route:
            result = not_routed(
                session,
                conversation_id,
                "This question was not routed to screen, compare, share, or gap. There is no scored answer.",
            )
            return explain_locally(redact_result(result, allow_costs)), result
        arguments = dict(route.get("arguments") or {})
        if route.get("name") == "screen" and not arguments.get("project_type"):
            carried = _earlier_project_type(session, conversation_id)
            latest = turns[-1]["content"] if turns else ""
            named = _project(latest.lower())
            if carried:
                arguments["project_type"] = carried
            elif named:
                arguments["project_type"] = named
        result = run_tool(session, route.get("name") or "", arguments, conversation_id)
        return explain_locally(result if allow_costs else redact_result(result, False)), result


def _single_airport_id(session: Session, text: str) -> int | None:
    found = []
    for code in re.findall(r"\b[A-Z]{3,4}\b", text):
        status, airport, _matches = lookup_airport(session, code)
        if status == "found" and airport.id not in found:
            found.append(airport.id)
    if len(found) == 1:
        return found[0]
    return None


def _messages(session: Session, conversation_id: int) -> list[Message]:
    return list(
        session.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)).all()
    )


def _earlier_project_type(session: Session, conversation_id: int) -> str | None:
    rows = session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role == "assistant")
        .order_by(Message.id.desc())
    ).all()
    for row in rows:
        if not row.result_json:
            continue
        try:
            payload = json.loads(row.result_json)
        except json.JSONDecodeError:
            continue
        if payload.get("function") == "screen" and payload.get("project_type"):
            return payload["project_type"]
    return None
