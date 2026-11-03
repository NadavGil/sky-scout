import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Observation
from app.scoring.explain import explain_locally
from app.scoring.service import share
from app.services.catalog import import_catalog
from app.services.stats import import_t100_csv
from app.db import session_scope
from tests.samples import airports_csv

ROOT = Path(__file__).resolve().parents[1]


class ScriptedModel:
    name = "stub"

    def __init__(self, routes):
        self.routes = list(routes)
        self.turns = []
        self.explained = []

    async def route(self, turns):
        self.turns.append(turns)
        return self.routes.pop(0)

    async def explain(self, skill, result):
        self.explained.append(result)
        return explain_locally(result)


def _catalog() -> str:
    rows = []
    scheduled = [
        ("KBOS", "BOS", "Boston Logan", "Boston", "US-MA", 42.36, -71.01),
        ("KBDL", "BDL", "Bradley", "Windsor Locks", "US-CT", 41.94, -72.68),
        ("KPWM", "PWM", "Portland Jetport", "Portland", "US-ME", 43.65, -70.31),
        ("KMHT", "MHT", "Manchester", "Manchester", "US-NH", 42.93, -71.44),
        ("KPVD", "PVD", "Providence", "Providence", "US-RI", 41.72, -71.43),
        ("KBTV", "BTV", "Burlington", "Burlington", "US-VT", 44.47, -73.15),
        ("KLAX", "LAX", "Los Angeles International", "Los Angeles", "US-CA", 33.94, -118.41),
        ("KWHP", "WHP", "Whiteman", "Los Angeles", "US-CA", 34.26, -118.41),
        ("KSNA", "SNA", "John Wayne", "Santa Ana", "US-CA", 33.68, -117.87),
        ("PANC", "ANC", "Ted Stevens Anchorage", "Anchorage", "US-AK", 61.17, -150.00),
        ("KSFO", "SFO", "San Francisco", "San Francisco", "US-CA", 37.62, -122.38),
    ]
    for index, (ident, code, name, city, region, lat, lon) in enumerate(scheduled, start=1):
        rows.append(
            {
                "id": str(index),
                "ident": ident,
                "type": "large_airport",
                "name": name,
                "latitude_deg": str(lat),
                "longitude_deg": str(lon),
                "iso_country": "US",
                "iso_region": region,
                "municipality": city,
                "scheduled_service": "yes",
                "gps_code": ident,
                "iata_code": code,
                "local_code": code,
            }
        )
    rows.append(
        {
            "id": "90",
            "ident": "KOLD",
            "type": "closed",
            "name": "Closed Boston Field",
            "latitude_deg": "42.3",
            "longitude_deg": "-71.1",
            "iso_country": "US",
            "iso_region": "US-MA",
            "municipality": "Boston",
            "scheduled_service": "no",
            "gps_code": "KOLD",
        }
    )
    rows.append(
        {
            "id": "91",
            "ident": "TJSJ",
            "type": "large_airport",
            "name": "San Juan",
            "latitude_deg": "18.44",
            "longitude_deg": "-66.00",
            "iso_country": "US",
            "iso_region": "US-PR",
            "municipality": "San Juan",
            "scheduled_service": "yes",
            "gps_code": "TJSJ",
            "iata_code": "SJU",
            "local_code": "SJU",
        }
    )
    return airports_csv(rows)


def _t100() -> str:
    lines = ["ORIGIN,DEST,YEAR,MONTH,DEPARTURES_PERFORMED,DISTANCE"]
    for month in range(1, 13):
        lines.append(f"ANC,NRT,{2024},{month},10,3460")
        lines.append(f"ANC,SEA,{2024},{month},30,1448")
        lines.append(f"ANC,ZZZ,{2024},{month},4,")
    return "\n".join(lines) + "\n"


def test_chat_session(tmp_path):
    model = ScriptedModel(
        [
            {"name": "screen", "arguments": {"region": "New England"}},
            {"name": "screen", "arguments": {"region": "Pacific"}},
            {"name": "compare", "arguments": {"airports": ["LA", "Santa Ana"], "metric": "congestion", "period": "2024-06"}},
            {"name": "share", "arguments": {"airport": "ANC", "category": "long_haul", "period": "2024"}},
            {"name": "gap", "arguments": {"airport": "SFO", "quantity": "unmet_demand"}},
            {"name": "screen", "arguments": {"region": "New England", "project_type": "terminal expansion"}},
        ]
    )
    database = tmp_path / "sky-scout.db"
    app = create_app(
        Settings(database_url=f"sqlite:///{database}", gemini_api_key="", allow_project_costs_to_model=False),
        sync_on_startup=False,
        model=model,
    )
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Sky Scout" in page.text
        assert ">Send<" in page.text
        assert "New chat" in page.text
        assert ">Record<" in page.text
        assert "Read aloud" in page.text

        with session_scope(app.state.session_factory) as session:
            import_catalog(session, _catalog(), "https://davidmegginson.github.io/ourairports-data/airports.csv")
            lax = client.get("/airports/LAX").json()
            session.add(
                Observation(
                    airport_id=lax["id"],
                    metric="aircraft_in_bbox",
                    value=99,
                    unit="aircraft",
                    source="opensky",
                    retrieved_at=__import__("app.clock", fromlist=["utcnow"]).utcnow(),
                    value_kind="measured",
                    quality_flags="[]",
                    coverage_note="receiver sample",
                )
            )
            session.commit()

        conversation = client.post("/conversations")
        assert conversation.status_code == 200
        conversation_id = conversation.json()["id"]

        screen = _ask(client, conversation_id, "Where should we look for terminal expansion in New England?")
        assert screen["result"]["status"] == "insufficient_data"
        assert screen["result"]["ranked"] == []
        assert screen["result"]["peer_count"] == 6
        assert "US-PR" not in {item["iso_region"] for item in screen["result"]["airports"]}
        assert all("opportunity_score" not in item for item in screen["result"]["airports"])
        for name in ("Connecticut", "Maine", "Massachusetts", "New Hampshire", "Rhode Island", "Vermont"):
            assert name in screen["reply"]
        assert "Scheduled commercial-service airports" in screen["reply"]
        assert "Territories are excluded." in screen["reply"]
        assert "There is no rank." in screen["reply"]
        assert screen["result"]["project_type"] == "terminal expansion"

        follow = _ask(client, conversation_id, "What about the Pacific?")
        assert follow["result"]["region"] == "Pacific"
        assert follow["result"]["project_type"] == "terminal expansion"
        assert "CA" in follow["result"]["states"]

        compared = _ask(client, conversation_id, "Compare congestion at LA and Santa Ana for 2024-06.")
        assert compared["result"]["status"] == "unavailable"
        assert compared["result"]["substituted_live_traffic"] is False
        assert compared["result"]["values"] is None
        assert "99" not in compared["reply"]
        assert "A live aircraft count was not used." in compared["reply"]
        assert "Whiteman" in compared["reply"]

        with session_scope(app.state.session_factory) as session:
            import_t100_csv(session, _t100(), "https://transtats.bts.gov/example/t100.csv")
            expected = share(session, "ANC", "long_haul", "2024")

        shared = _ask(client, conversation_id, "What share of Anchorage departures in 2024 were long-haul?")
        assert shared["result"]["percentage"] == expected["percentage"] == 25.0
        assert shared["result"]["excluded_destinations"] == expected["excluded_destinations"] == 1
        assert "3000" in shared["reply"]
        assert "Excluded destinations: 1." in shared["reply"]
        assert "Percentage: 25.0." in shared["reply"]
        assert shared["result"]["period"]["complete_year"] is True

        missing = _ask(client, conversation_id, "What is unmet demand at SFO?")
        assert missing["result"]["status"] == "unknown"
        assert missing["result"]["value"] is None
        assert missing["result"]["missing"] == ["unconstrained_demand", "effective_capacity"]
        assert "Unmet demand is unknown." in missing["reply"]
        assert "Served traffic" in missing["reply"]

        costly = _ask(
            client,
            conversation_id,
            "Project cost: 400 million USD\nIs a terminal expansion at BOS profitable?",
        )
        evidence = costly["result"]["evidence"][-1]
        assert evidence["kind"] == "project_cost"
        assert evidence["value_text"] == "400 million USD"
        assert evidence["value_kind"] == "analyst_assumed"
        assert costly["result"]["profitability"]["claimed"] is False
        assert "not a measured API value" in costly["reply"]
        assert "Profitability is not claimed." in costly["reply"]
        assert "400 million" not in costly["reply"]
        assert model.explained == []
        assert "400 million" not in json.dumps(model.turns[-1])
        with session_scope(app.state.session_factory) as session:
            stored = session.query(Observation).all()
            assert all("400 million" not in (item.detail or "") and item.metric != "project_cost" for item in stored)


def _ask(client, conversation_id, text):
    response = client.post(f"/conversations/{conversation_id}/messages", json={"content": text})
    assert response.status_code == 200
    return response.json()


def test_skill_copied_verbatim():
    copied = ROOT / ".cursor" / "skills" / "us-airport-renovation-opportunity-ranking" / "SKILL.md"
    original = Path("/Users/nadavgilron/Downloads/SKILL.md")
    assert copied.read_bytes() == original.read_bytes()
