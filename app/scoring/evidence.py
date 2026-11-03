import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.models import AnalystEvidence

EVIDENCE_KINDS = ("bottleneck", "intervention", "project_cost", "capacity_mapping", "cash_flows")
COST_LINE = re.compile(r"(?im)^.*\bproject\s+cost\b.*$")
CASH_BLOCK = re.compile(r"(?ims)^\s*cash\s+flows\s*:\s*\n.*?(?:\n\s*\n|\Z)")
MONEY = re.compile(
    r"(?i)(\$[\d,]+(?:\.\d+)?(?:\s*(?:million|billion))?|\b\d[\d,]*(?:\.\d+)?\s*(?:usd|dollars|million|billion)\b)"
)
FIELD_PATTERNS = (
    ("project_cost", re.compile(r"(?im)^\s*project\s+cost\s*:\s*(.+?)\s*$")),
    ("bottleneck", re.compile(r"(?im)^\s*bottleneck\s*:\s*(.+?)\s*$")),
    ("intervention", re.compile(r"(?im)^\s*intervention\s*:\s*(.+?)\s*$")),
    ("capacity_mapping", re.compile(r"(?im)^\s*capacity\s+mapping\s*:\s*(.+?)\s*$")),
    ("cash_flows", re.compile(r"(?ims)^\s*cash\s+flows\s*:\s*\n(.+?)(?:\n\s*\n|\Z)")),
)


def extract_evidence(text: str) -> list[tuple[str, str]]:
    found = []
    for kind, pattern in FIELD_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1).strip()
            if value:
                found.append((kind, value))
    return found


def redact_for_model(text: str, allow_costs: bool) -> str:
    if allow_costs:
        return text
    redacted = CASH_BLOCK.sub("[cash flows withheld]\n", text)
    redacted = COST_LINE.sub("[project cost withheld]", redacted)
    return MONEY.sub("[amount withheld]", redacted)


def record_analyst_evidence(
    session: Session,
    conversation_id: int,
    kind: str,
    value_text: str,
    airport_id: int | None = None,
) -> dict:
    if kind not in EVIDENCE_KINDS:
        return {"error": "kind must be bottleneck, intervention, project_cost, capacity_mapping, or cash_flows"}
    text = value_text.strip()
    if not text:
        return {"error": "value_text is empty"}
    row = AnalystEvidence(
        conversation_id=conversation_id,
        airport_id=airport_id,
        kind=kind,
        value_text=text,
        value_kind="analyst_assumed",
        supplied_at=clock.utcnow(),
    )
    session.add(row)
    session.flush()
    return _payload(row)


def list_evidence(session: Session, conversation_id: int | None) -> list[dict]:
    if conversation_id is None:
        return []
    rows = session.scalars(
        select(AnalystEvidence)
        .where(AnalystEvidence.conversation_id == conversation_id)
        .order_by(AnalystEvidence.id)
    ).all()
    return [_payload(row) for row in rows]


def redact_result(result: dict, allow_costs: bool) -> dict:
    if allow_costs:
        return result
    cloned = {
        key: (list(value) if isinstance(value, list) else value)
        for key, value in result.items()
    }
    cloned["evidence"] = []
    for item in result.get("evidence", []):
        copy = dict(item)
        if copy.get("kind") in {"project_cost", "cash_flows"}:
            copy["value_text"] = "[withheld]"
        cloned["evidence"].append(copy)
    return cloned


def _payload(row: AnalystEvidence) -> dict:
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "airport_id": row.airport_id,
        "kind": row.kind,
        "value_text": row.value_text,
        "value_kind": row.value_kind,
        "supplied_at": row.supplied_at.isoformat() + "Z",
    }
