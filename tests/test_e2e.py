import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.samples import CATALOG_CSV
from tests.transport import ScriptedTransport


def test_data_service_session(tmp_path):
    database = tmp_path / "sky-scout.db"
    url = f"sqlite:///{database}"
    transport = ScriptedTransport()
    transport.respond("GET", "ourairports-data/airports.csv", text=CATALOG_CSV)
    transport.respond(
        "GET",
        "/states/all",
        json_body={"time": 1_700_000_000, "states": [["abc", "CALL"], ["def", "CALL2"]]},
    )
    transport.respond("GET", "/metar", json_body=[{"rawOb": "METAR KJFK"}])
    transport.respond("GET", "/taf", json_body=[{"rawTAF": "TAF KJFK"}])
    settings = Settings(database_url=url, opensky_client_id="", opensky_client_secret="")
    app = create_app(settings, transport=transport, sync_on_startup=False)

    with TestClient(app) as client:
        health = client.get("/health").json()
        assert health["last_airport_sync_at"] is None
        assert health["opensky_flight_credentials_configured"] is False

        synced = client.post("/airports/sync")
        assert synced.status_code == 200
        by_name = client.get("/airports", params={"q": "Kennedy"}).json()
        by_icao = client.get("/airports/KJFK").json()
        by_iata = client.get("/airports/JFK").json()
        assert by_name[0]["id"] == by_icao["id"] == by_iata["id"]
        assert {item["scheme"] for item in by_icao["identifiers"]} >= {
            "icao",
            "iata",
            "gps",
            "ourairports_ident",
        }

        traffic = client.get("/airports/KJFK/traffic")
        assert traffic.status_code == 200
        assert traffic.json()["observation"]["metric"] == "aircraft_in_bbox"
        cached = client.get("/airports/KJFK/traffic")
        assert cached.json()["cached"] is True
        assert len(transport.matching("/states/all")) == 1
        assert client.get("/airports/KJFK/traffic", params={"refresh": "true"}).status_code == 200
        assert len(transport.matching("/states/all")) == 2

        weather = client.get("/airports/KJFK/weather")
        assert weather.json()["metar"]["observation"]["quality_flags"] == ["context_only"]
        assert weather.json()["taf"]["observation"]["quality_flags"] == ["context_only"]
        again = client.get("/airports/KJFK/weather")
        assert again.json()["metar"]["cached"] is True
        assert again.json()["taf"]["cached"] is True
        assert len(transport.matching("/metar")) == 1
        assert len(transport.matching("/taf")) == 1

        arrivals = client.get("/airports/KJFK/arrivals")
        assert arrivals.status_code == 503
        assert transport.matching("/flights/") == []

        observations = client.get("/airports/KJFK/observations").json()
        traffic_rows = [row for row in observations if row["metric"] == "aircraft_in_bbox"]
        assert traffic_rows
        assert traffic_rows[0]["retrieved_at"]
        assert "Not annual passengers, seats, or movements" in traffic_rows[0]["coverage_note"]
        snapshots = client.get("/airports/KJFK/snapshots").json()
        assert snapshots
        assert snapshots[0]["retrieved_at"]

    restart = ScriptedTransport()
    restart.respond("GET", "ourairports-data/airports.csv", text=CATALOG_CSV)

    def fail_states(_request):
        return httpx.Response(500, json={"error": "down"})

    restart.route("GET", "/states/all", fail_states)
    restarted = create_app(settings, transport=restart, sync_on_startup=True)
    with TestClient(restarted) as client:
        assert client.get("/airports/JFK").json()["name"] == "John F Kennedy International"
        assert restart.matching("airports.csv") == []
        failed = client.get("/airports/KJFK/traffic", params={"refresh": "true"})
        assert failed.status_code == 200, failed.text
        assert failed.json()["refresh_error"] == "HTTP 500"
        assert failed.json()["observation"]["value"] == 2
        values = [
            row["value"]
            for row in client.get("/airports/KJFK/observations", params={"metric": "aircraft_in_bbox"}).json()
        ]
        assert 0 not in values
