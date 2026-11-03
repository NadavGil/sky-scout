import json
import os
import sys
from pathlib import Path

from app.config import Settings
from app.db import prepare_database, session_scope
from app.mcp.tools import TOOL_NAMES, call_tool

SCHEMAS = {
    "screen": {
        "type": "object",
        "properties": {
            "region": {"type": "string"},
            "project_type": {"type": "string"},
            "conversation_id": {"type": "integer"},
        },
        "required": ["region"],
    },
    "compare": {
        "type": "object",
        "properties": {
            "airports": {"type": "array", "items": {"type": "string"}},
            "metric": {"type": "string"},
            "period": {"type": "string"},
            "conversation_id": {"type": "integer"},
        },
        "required": ["airports", "metric"],
    },
    "share": {
        "type": "object",
        "properties": {
            "airport": {"type": "string"},
            "category": {"type": "string"},
            "period": {"type": "string"},
            "conversation_id": {"type": "integer"},
        },
        "required": ["airport", "category"],
    },
    "gap": {
        "type": "object",
        "properties": {
            "airport": {"type": "string"},
            "quantity": {"type": "string"},
            "conversation_id": {"type": "integer"},
        },
        "required": ["airport", "quantity"],
    },
    "search_airports": {
        "type": "object",
        "properties": {"q": {"type": "string"}, "country": {"type": "string"}, "limit": {"type": "integer"}},
    },
    "get_observations": {
        "type": "object",
        "properties": {"code": {"type": "string"}, "metric": {"type": "string"}},
        "required": ["code"],
    },
    "record_analyst_evidence": {
        "type": "object",
        "properties": {
            "conversation_id": {"type": "integer"},
            "kind": {"type": "string"},
            "value_text": {"type": "string"},
            "airport_id": {"type": "integer"},
        },
        "required": ["conversation_id", "kind", "value_text"],
    },
    "brief": {
        "type": "object",
        "properties": {"conversation_id": {"type": "integer"}, "word_limit": {"type": "integer"}},
    },
    "method": {
        "type": "object",
        "properties": {"conversation_id": {"type": "integer"}, "topic": {"type": "string"}},
    },
}


def tool_list() -> list[dict]:
    descriptions = {
        "screen": "Screen a U.S. region. Returns scoring JSON only.",
        "compare": "Compare airports on one metric and period. Returns scoring JSON only.",
        "share": "Share of a flight category. Returns scoring JSON only.",
        "gap": "A missing quantity such as unmet demand. Returns scoring JSON only.",
        "search_airports": "Read airports from the store.",
        "get_observations": "Read stored observations for one airport.",
        "record_analyst_evidence": (
            "Store a bottleneck, intervention, project cost, capacity mapping, or cash-flow schedule as analyst-supplied."
        ),
        "brief": "Summarize the previous scored answer in this conversation. Returns scoring JSON only.",
        "method": "State the bottleneck evidence rule. Returns scoring JSON only.",
    }
    return [
        {"name": name, "description": descriptions[name], "inputSchema": SCHEMAS[name]}
        for name in TOOL_NAMES
    ]


def handle(message: dict, session_factory) -> dict | None:
    method = message.get("method")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "sky-scout", "version": "0.1"},
        }
    elif method == "tools/list":
        result = {"tools": tool_list()}
    elif method == "tools/call":
        params = message.get("params") or {}
        with session_scope(session_factory) as session:
            try:
                payload = call_tool(session, params.get("name"), params.get("arguments") or {})
                session.commit()
            except (KeyError, TypeError, ValueError) as exc:
                payload = {"error": str(exc)}
        result = {
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "isError": "error" in payload,
        }
    else:
        return {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": message.get("id"), "result": result}


def serve(session_factory) -> None:
    while True:
        message = _read()
        if message is None:
            return
        response = handle(message, session_factory)
        if response is not None:
            _write(response)


def _read() -> dict | None:
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if line == b"":
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, value = line.decode().split(":", 1)
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    return json.loads(sys.stdin.buffer.read(length))


def _write(payload: dict) -> None:
    body = json.dumps(payload).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    sys.stdout.buffer.flush()


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    os.chdir(root)
    serve(prepare_database(Settings()))


if __name__ == "__main__":
    main()
