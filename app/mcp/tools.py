from sqlalchemy.orm import Session

from app.scoring.evidence import record_analyst_evidence
from app.scoring.service import get_observations, run_tool, search_airports_json

TOOL_NAMES = (
    "screen",
    "compare",
    "share",
    "gap",
    "search_airports",
    "get_observations",
    "record_analyst_evidence",
    "brief",
    "method",
)


def call_tool(session: Session, name: str, arguments: dict | None) -> dict:
    arguments = arguments or {}
    if name == "search_airports":
        return search_airports_json(
            session,
            arguments.get("q"),
            arguments.get("country", "US"),
            int(arguments.get("limit", 20)),
        )
    if name == "get_observations":
        return get_observations(session, arguments.get("code") or "", arguments.get("metric"))
    if name == "record_analyst_evidence":
        return record_analyst_evidence(
            session,
            int(arguments["conversation_id"]),
            arguments.get("kind") or "",
            arguments.get("value_text") or "",
            arguments.get("airport_id"),
        )
    return run_tool(session, name, arguments, arguments.get("conversation_id"))
