import asyncio
import threading
from datetime import timedelta

from fastapi.testclient import TestClient

from app import clock
from app.config import Settings
from app.db import session_scope
from app.main import create_app
from app.models import DatasetSync
from tests.samples import CATALOG_CSV, renamed_catalog
from tests.transport import ScriptedTransport


def _sync(client):
    response = client.post("/airports/sync")
    assert response.status_code == 200, response.text
    return response.json()


def _schemes(payload):
    return {item["scheme"]: item for item in payload["identifiers"]}


def test_sync_upserts_and_keeps_one_airport(client):
    first = _sync(client)
    assert first["row_count"] == 6
    client.app.state.transport.respond(
        "GET",
        "ourairports-data/airports.csv",
        text=renamed_catalog(),
    )
    _sync(client)
    found = client.get("/airports", params={"q": "Kennedy"}).json()
    assert len(found) == 1
    assert found[0]["name"] == "Renamed Kennedy"


def test_closed_and_non_us_airports_stay_searchable(client):
    _sync(client)
    closed = client.get("/airports", params={"q": "Old Field"}).json()
    assert closed[0]["airport_type"] == "closed"
    heathrow = client.get("/airports", params={"country": "GB"}).json()
    assert [item["name"] for item in heathrow] == ["London Heathrow"]


def test_failed_download_keeps_the_catalog(client):
    _sync(client)
    client.app.state.transport.respond("GET", "ourairports-data/airports.csv", status=500, json_body={"error": "down"})
    response = client.post("/airports/sync")
    assert response.status_code == 502
    assert client.get("/airports/KJFK").json()["name"] == "John F Kennedy International"


def test_identifier_schemes_stay_separate(client):
    _sync(client)
    kennedy = client.get("/airports/KJFK").json()
    schemes = _schemes(kennedy)
    assert schemes["ourairports_ident"]["value"] == "KJFK"
    assert schemes["icao"]["value"] == "KJFK"
    assert schemes["iata"]["value"] == "JFK"
    assert "faa_lid" not in schemes

    small = client.get("/airports/00AA").json()
    small_schemes = _schemes(small)
    assert small_schemes["ourairports_ident"]["value"] == "00AA"
    assert small_schemes["faa_lid"]["value"] == "1A0"
    assert "icao" not in small_schemes
    assert "iata" not in small_schemes
    assert small_schemes["faa_lid"]["provenance"]


def test_ambiguous_code_is_not_resolved_silently(client):
    _sync(client)
    first = client.get("/airports/KDUP").json()
    second = client.get("/airports/EDUP").json()
    assert _schemes(first)["iata"]["ambiguous"] is True
    assert _schemes(second)["iata"]["ambiguous"] is True
    response = client.get("/airports/DUP")
    assert response.status_code == 409
    assert len(response.json()["detail"]["matches"]) == 2


def test_search_matches_name_city_and_codes_once(client):
    _sync(client)
    by_name = client.get("/airports", params={"q": "kennedy"}).json()
    by_city = client.get("/airports", params={"q": "new york"}).json()
    by_iata = client.get("/airports", params={"q": "jfk"}).json()
    by_faa = client.get("/airports", params={"q": "1a0"}).json()
    assert [item["name"] for item in by_name] == ["John F Kennedy International"]
    assert [item["name"] for item in by_city] == ["John F Kennedy International"]
    assert len(by_iata) == 1
    assert by_faa[0]["name"] == "Small Field"
    limited = client.get("/airports", params={"country": "US", "limit": 1}).json()
    assert len(limited) == 1
    assert client.get("/airports", params={"q": "no-such-airport"}).json() == []
    assert client.get("/airports/NOPE").status_code == 404


def test_lookup_names_the_matched_scheme(client):
    _sync(client)
    by_icao = client.get("/airports/KJFK").json()
    by_iata = client.get("/airports/JFK").json()
    by_faa = client.get("/airports/1A0").json()
    assert by_icao["id"] == by_iata["id"]
    assert by_icao["matched_scheme"] == "icao"
    assert by_iata["matched_scheme"] == "iata"
    assert by_faa["matched_scheme"] == "faa_lid"
    assert {item["scheme"] for item in by_icao["identifiers"]} >= {"icao", "iata", "gps", "ourairports_ident"}


def test_startup_empty_database_downloads_once(settings):
    transport = ScriptedTransport()
    transport.respond("GET", "ourairports-data/airports.csv", text=CATALOG_CSV)
    app = create_app(settings, transport=transport, sync_on_startup=True)
    with TestClient(app) as client:
        assert client.get("/airports/JFK").status_code == 200
    assert len(transport.matching("airports.csv")) == 1


def test_startup_with_fresh_sync_does_not_download(tmp_path):
    database = tmp_path / "fresh.db"
    url = f"sqlite:///{database}"
    first = ScriptedTransport()
    first.respond("GET", "ourairports-data/airports.csv", text=CATALOG_CSV)
    settings = Settings(database_url=url, opensky_client_id="", opensky_client_secret="")
    with TestClient(create_app(settings, transport=first, sync_on_startup=True)):
        pass
    second = ScriptedTransport()
    second.respond("GET", "ourairports-data/airports.csv", status=500, json_body={"error": "should not be called"})
    with TestClient(create_app(settings, transport=second, sync_on_startup=True)) as client:
        assert client.get("/airports/JFK").status_code == 200
    assert second.matching("airports.csv") == []


def test_stale_catalog_is_served_while_refresh_runs(tmp_path):
    database = tmp_path / "stale.db"
    url = f"sqlite:///{database}"
    settings = Settings(database_url=url, opensky_client_id="", opensky_client_secret="")
    initial = ScriptedTransport()
    initial.respond("GET", "ourairports-data/airports.csv", text=CATALOG_CSV)
    with TestClient(create_app(settings, transport=initial, sync_on_startup=True)):
        pass
    with session_scope(create_app(settings, sync_on_startup=False).state.session_factory) as session:
        sync = session.query(DatasetSync).one()
        sync.fetched_at = clock.utcnow() - timedelta(hours=48)
        session.commit()

    gate = threading.Event()
    refresh = ScriptedTransport()

    async def slow(_request):
        await asyncio.to_thread(gate.wait, 5)
        return __import__("httpx").Response(200, text=renamed_catalog())

    refresh.route("GET", "airports.csv", slow)
    app = create_app(settings, transport=refresh, sync_on_startup=True)
    with TestClient(app) as client:
        assert client.get("/airports/KJFK").json()["name"] == "John F Kennedy International"
        gate.set()
        renamed = False
        for _ in range(50):
            if client.get("/airports/KJFK").json()["name"] == "Renamed Kennedy":
                renamed = True
                break
        assert renamed
    assert refresh.matching("states/all") == []


def test_directory_sync_does_not_call_live_traffic(client):
    _sync(client)
    assert client.app.state.transport.matching("states/all") == []


def test_health_reports_sync_and_credentials(client, settings):
    before = client.get("/health").json()
    assert before["last_airport_sync_at"] is None
    assert before["opensky_flight_credentials_configured"] is False
    _sync(client)
    after = client.get("/health").json()
    assert after["last_airport_sync_at"] is not None

    configured = Settings(
        database_url="sqlite://",
        opensky_client_id="client",
        opensky_client_secret="secret",
    )
    app = create_app(configured, transport=ScriptedTransport(), sync_on_startup=False)
    with TestClient(app) as configured_client:
        assert configured_client.get("/health").json()["opensky_flight_credentials_configured"] is True
