import re

from app.scoring.explain import explain_locally
from app.scoring.geography import REGIONS, STATE_NAMES


class LocalModel:
    name = "scoring_module"

    async def route(self, turns: list[dict]) -> dict | None:
        text = turns[-1]["content"] if turns else ""
        special = follow_up(text)
        if special:
            return special
        lowered = text.lower()
        if "unmet" in lowered:
            return {"name": "gap", "arguments": {"airport": _airport(text) or "SFO", "quantity": "unmet_demand"}}
        if "long-haul" in lowered or "long haul" in lowered:
            return {
                "name": "share",
                "arguments": {
                    "airport": _airport(text) or "",
                    "category": "long_haul",
                    "period": _year(text),
                },
            }
        if "congestion" in lowered or "compare" in lowered:
            return {
                "name": "compare",
                "arguments": {"airports": _compare_airports(text), "metric": "congestion", "period": _month(text)},
            }
        region = _region(lowered)
        if region is None:
            return None
        return {"name": "screen", "arguments": {"region": region, "project_type": _project(lowered)}}

    async def explain(self, skill: str, result: dict) -> str:
        return explain_locally(result)


def follow_up(text: str) -> dict | None:
    lowered = text.lower()
    if _is_brief(lowered):
        match = re.search(r"\b(\d+)\s+words\b", lowered)
        return {"name": "brief", "arguments": {"word_limit": int(match.group(1)) if match else 150}}
    if _is_method(lowered):
        return {"name": "method", "arguments": {"topic": "bottleneck"}}
    match = re.search(
        r"\bcompare\s+(.+?)\s+(?:in\s+|for\s+)?(last year|this year|last month|this month|20\d{2}(?:-\d{2})?)\s+"
        r"with\s+(.+?)\s+(?:in\s+|for\s+)?(last year|this year|last month|this month|20\d{2}(?:-\d{2})?)\b",
        text,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return {
        "name": "compare",
        "arguments": {
            "airports": [_clean_place(match.group(1)), _clean_place(match.group(3))],
            "metric": "reported_history",
            "periods": [match.group(2), match.group(4)],
        },
    }


def _is_brief(lowered: str) -> bool:
    if "summarize" in lowered or "summary of" in lowered or "sum up" in lowered:
        return True
    return "biggest uncertainty" in lowered and ("source" in lowered or "assumption" in lowered or "recommendation" in lowered)


def _is_method(lowered: str) -> bool:
    if "bottleneck" not in lowered:
        return False
    if "distinguish" in lowered or "difference between" in lowered or "what evidence" in lowered:
        return True
    return sum(name in lowered for name in ("terminal", "runway", "airspace")) >= 2


def _clean_place(text: str) -> str:
    cleaned = re.sub(r"(?i)\b(congestion|levels?|airport|international|the|at)\b", " ", text)
    return " ".join(cleaned.replace(",", " ").split())


def _region(lowered: str) -> str | None:
    if "district of columbia" in lowered or "washington, dc" in lowered or "washington dc" in lowered:
        return "District of Columbia"
    keys = sorted(REGIONS, key=len, reverse=True)
    for key in keys:
        if key in {"us", "usa"}:
            continue
        if key in lowered:
            return "United States" if key == "united states" else key.title()
    for code, name in STATE_NAMES.items():
        if name.lower() in lowered:
            return name
    return None


def _project(lowered: str) -> str | None:
    if "terminal" in lowered:
        return "terminal expansion"
    if "runway" in lowered:
        return "runway project"
    return None


def _year(text: str) -> str | None:
    match = re.search(r"\b(20\d{2})\b", text)
    return match.group(1) if match else None


def _month(text: str) -> str | None:
    match = re.search(r"\b(20\d{2})-(\d{2})\b", text)
    return match.group(0) if match else None


def _airport(text: str) -> str:
    lowered = text.lower()
    if "anchorage" in lowered:
        return "Anchorage"
    if "santa ana" in lowered:
        return "Santa Ana"
    if re.search(r"\bla\b", lowered):
        return "LA"
    codes = re.findall(r"\b[A-Z]{3}\b", text)
    return codes[0] if codes else ""


def _compare_airports(text: str) -> list[str]:
    lowered = text.lower()
    found = []
    if re.search(r"\bla\b", lowered):
        found.append("LA")
    if "santa ana" in lowered:
        found.append("Santa Ana")
    for code in re.findall(r"\b[A-Z]{3}\b", text):
        if code not in found and code != "LA":
            found.append(code)
    return found
