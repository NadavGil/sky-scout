import json
import logging
from datetime import timedelta

import httpx

from app import clock
from app.config import Settings
from app.db import session_scope
from app.main import create_app
from app.models import Observation, RawSnapshot
from app.services.live import yesterday_window
from app.services.retention import purge_snapshots
from fastapi.testclient import TestClient
from tests.samples import CATALOG_CSV
from tests.transport import ScriptedTransport


def _states(count):
    return {"time": 1_700_000_000, "states": [["abc", "CALL"] for _ in range(count)]}


def _sync(client):
    assert client.post("/airports/sync").status_code == 200


def test_traffic_stores_bbox_sample_and_cache(client, transport):
    transport.respond("GET", "/states/all", json_body=_states(2))
    _sync(client)
    first = client.get("/airports/KJFK/traffic")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["cached"] is False
    assert body["observation"]["metric"] == "aircraft_in_bbox"
    assert body["observation"]["value"] == 2
    assert body["observation"]["unit"] == "aircraft"
    assert body["observation"]["value_kind"] == "measured"
    assert "Not annual passengers, seats, or movements" in body["observation"]["coverage_note"]
    params = httpx.URL(transport.matching("/states/all")[0].split(" ", 1)[1]).params
    assert float(params["lamin"]) < 40.6398 < float(params["lamax"])
    assert float(params["lomin"]) < -73.7789 < float(params["lomax"])

    cached = client.get("/airports/KJFK/traffic").json()
    assert cached["cached"] is True
    assert len(transport.matching("/states/all")) == 1

    refreshed = client.get("/airports/KJFK/traffic", params={"refresh": "true"})
    assert refreshed.status_code == 200
    assert len(transport.matching("/states/all")) == 2
    assert client.get("/airports/NOPE/traffic").status_code == 404
    assert len(transport.matching("/states/all")) == 2


def test_empty_state_list_is_measured_zero_and_missing_list_is_null(client, transport):
    _sync(client)
    transport.respond("GET", "/states/all", json_body={"time": 10, "states": []})
    empty = client.get("/airports/KJFK/traffic").json()
    assert empty["observation"]["value"] == 0

    transport.respond("GET", "/states/all", json_body={"time": 10})
    missing = client.get("/airports/KJFK/traffic", params={"refresh": "true"}).json()
    assert missing["observation"]["value"] is None
    assert "missing_states" in missing["observation"]["quality_flags"]


def test_timeout_keeps_last_traffic_row(client, transport):
    _sync(client)
    transport.respond("GET", "/states/all", json_body=_states(2))
    assert client.get("/airports/KJFK/traffic").status_code == 200

    def explode(_request):
        raise httpx.TimeoutException("timed out")

    transport.route("GET", "/states/all", explode)
    failed = client.get("/airports/KJFK/traffic", params={"refresh": "true"})
    assert failed.status_code == 200, failed.text
    assert failed.json()["observation"]["value"] == 2
    assert failed.json()["refresh_error"] == "timeout"
    listed = client.get("/airports/KJFK/observations", params={"metric": "aircraft_in_bbox"}).json()
    assert [row["value"] for row in listed] == [2]


def test_rate_limit_retries_once(client, transport):
    _sync(client)
    seen = {"count": 0}

    def handler(_request):
        seen["count"] += 1
        if seen["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=_states(1))

    transport.route("GET", "/states/all", handler)

    async def sleeper(seconds):
        sleeper.waits.append(seconds)

    sleeper.waits = []
    client.app.state.sleeper = sleeper
    client.app.state.opensky.sleeper = sleeper
    response = client.get("/airports/KJFK/traffic")
    assert response.status_code == 200, response.text
    assert response.json()["observation"]["value"] == 1
    assert sleeper.waits == [0]


def test_flights_require_credentials_and_reject_long_spans(client, transport):
    _sync(client)
    denied = client.get("/airports/KJFK/arrivals")
    assert denied.status_code == 503
    assert client.get("/airports/KJFK/departures").status_code == 503
    assert transport.matching("/flights/") == []

    configured = _flight_client(transport)
    try:
        begin, end = 0, 3 * 24 * 60 * 60
        rejected = configured.get("/airports/KJFK/arrivals", params={"begin": begin, "end": end})
        assert rejected.status_code == 400
        assert transport.matching("/flights/") == []
    finally:
        configured.__exit__(None, None, None)


def test_arrivals_use_yesterday_and_cache_the_token_and_window(transport):
    client = _flight_client(transport)
    try:
        transport.respond("GET", "/flights/arrival", json_body=[{"icao24": "abc"}, {"icao24": "def"}])
        transport.respond("GET", "/flights/departure", json_body=[{"icao24": "zzz"}])
        first = client.get("/airports/KJFK/arrivals")
        assert first.status_code == 200, first.text
        assert first.json()["observation"]["metric"] == "arrival_flights"
        assert first.json()["observation"]["value"] == 2
        assert "Not annual passengers" in first.json()["observation"]["coverage_note"]
        url = httpx.URL(transport.matching("/flights/arrival")[0].split(" ", 1)[1])
        expected_begin, expected_end = yesterday_window()
        assert int(url.params["begin"]) == expected_begin
        assert int(url.params["end"]) == expected_end
        assert url.params["airport"] == "KJFK"

        cached = client.get("/airports/KJFK/arrivals")
        assert cached.json()["cached"] is True
        assert len(transport.matching("/flights/arrival")) == 1
        assert len(transport.matching("openid-connect/token")) == 1

        refreshed = client.get("/airports/KJFK/arrivals", params={"refresh": "true"})
        assert refreshed.status_code == 200
        assert len(transport.matching("/flights/arrival")) == 2
        assert len(transport.matching("openid-connect/token")) == 1

        departed = client.get("/airports/KJFK/departures")
        assert departed.json()["observation"]["value"] == 1
        assert transport.matching("/flights/departure")
    finally:
        client.__exit__(None, None, None)


def test_missing_flights_are_not_stored_as_zero(transport):
    client = _flight_client(transport)
    try:
        transport.respond("GET", "/flights/arrival", status=404, text="")
        missing = client.get("/airports/KJFK/arrivals").json()
        assert missing["observation"]["value"] is None
        values = [
            row["value"]
            for row in client.get("/airports/KJFK/observations", params={"metric": "arrival_flights"}).json()
        ]
        assert 0 not in values
    finally:
        client.__exit__(None, None, None)


def test_unauthorized_flights_refresh_the_token_once(transport):
    client = _flight_client(transport)
    try:
        seen = {"count": 0}

        def handler(_request):
            seen["count"] += 1
            if seen["count"] == 1:
                return httpx.Response(401, json={"error": "expired"})
            return httpx.Response(200, json=[{"icao24": "abc"}])

        transport.route("GET", "/flights/arrival", handler)
        response = client.get("/airports/KJFK/arrivals")
        assert response.status_code == 200, response.text
        assert response.json()["observation"]["value"] == 1
        assert len(transport.matching("openid-connect/token")) == 2
        assert seen["count"] == 2
    finally:
        client.__exit__(None, None, None)


def test_weather_is_context_and_cached(client, transport):
    _sync(client)
    transport.respond("GET", "/metar", json_body=[{"rawOb": "METAR KJFK"}])
    transport.respond("GET", "/taf", json_body=[{"rawTAF": "TAF KJFK"}])
    first = client.get("/airports/KJFK/weather")
    assert first.status_code == 200, first.text
    assert first.json()["metar"]["body"] == [{"rawOb": "METAR KJFK"}]
    assert first.json()["metar"]["observation"]["quality_flags"] == ["context_only"]
    assert first.json()["taf"]["observation"]["quality_flags"] == ["context_only"]
    second = client.get("/airports/KJFK/weather")
    assert second.json()["metar"]["cached"] is True
    assert second.json()["taf"]["cached"] is True
    assert len(transport.matching("/metar")) == 1
    assert len(transport.matching("/taf")) == 1
    assert client.get("/airports/NOPE/weather").status_code == 404
    assert len(transport.matching("/metar")) == 1


def test_weather_failure_keeps_the_last_report(client, transport):
    _sync(client)
    transport.respond("GET", "/metar", json_body=[{"rawOb": "METAR KJFK"}])
    transport.respond("GET", "/taf", json_body=[{"rawTAF": "TAF KJFK"}])
    assert client.get("/airports/KJFK/weather").status_code == 200

    def explode(_request):
        raise httpx.TimeoutException("timed out")

    transport.route("GET", "/metar", explode)
    transport.route("GET", "/taf", explode)
    failed = client.get("/airports/KJFK/weather", params={"refresh": "true"})
    assert failed.status_code == 200, failed.text
    assert failed.json()["metar"]["refresh_error"] == "timeout"
    assert failed.json()["metar"]["body"] == [{"rawOb": "METAR KJFK"}]
    metrics = {row["metric"] for row in client.get("/airports/KJFK/observations").json()}
    assert metrics.isdisjoint({"passengers", "seats", "enplanements", "capacity"})


def test_reads_are_newest_first_and_filterable(client, transport):
    _sync(client)
    transport.respond("GET", "/states/all", json_body=_states(1))
    client.get("/airports/KJFK/traffic")
    transport.respond("GET", "/states/all", json_body=_states(4))
    client.get("/airports/KJFK/traffic", params={"refresh": "true"})
    rows = client.get("/airports/KJFK/observations").json()
    assert [row["value"] for row in rows if row["metric"] == "aircraft_in_bbox"] == [4, 1]
    assert client.get("/airports/KJFK/observations", params={"metric": "aircraft_in_bbox"}).json()[0]["value"] == 4
    snapshots = client.get("/airports/KJFK/snapshots", params={"kind": "traffic"}).json()
    assert len(snapshots) == 2
    assert snapshots[0]["retrieved_at"] >= snapshots[1]["retrieved_at"]


def test_old_snapshots_are_deleted_and_observations_remain(client, transport):
    _sync(client)
    transport.respond("GET", "/states/all", json_body=_states(3))
    observation_id = client.get("/airports/KJFK/traffic").json()["observation"]["id"]
    with session_scope(client.app.state.session_factory) as session:
        snapshot = session.query(RawSnapshot).one()
        snapshot.retrieved_at = clock.utcnow() - timedelta(days=31)
        session.commit()
        removed = purge_snapshots(session, clock.utcnow() - timedelta(days=30))
        session.commit()
        assert removed == 1
        kept = session.get(Observation, observation_id)
        assert kept is not None
        assert kept.snapshot_id is None
        assert kept.value == 3


def test_logs_omit_the_client_secret(caplog, transport):
    caplog.set_level(logging.INFO, logger="sky_scout")
    client = _flight_client(transport, secret="super-secret-value", token="access-token-secret")
    try:
        transport.respond("GET", "/flights/arrival", json_body=[])
        assert client.get("/airports/KJFK/arrivals").status_code == 200
    finally:
        client.__exit__(None, None, None)
    assert "super-secret-value" not in caplog.text
    assert "access-token-secret" not in caplog.text


def _flight_client(transport, secret="super-secret-value", token="access-token-secret"):
    transport.respond(
        "POST",
        "openid-connect/token",
        json_body={"access_token": token, "expires_in": 1800},
    )
    if not any("airports.csv" in call for call in transport.calls):
        transport.respond("GET", "ourairports-data/airports.csv", text=CATALOG_CSV)
    settings = Settings(
        database_url="sqlite://",
        opensky_client_id="client-id",
        opensky_client_secret=secret,
    )
    app = create_app(settings, transport=transport, sync_on_startup=False)
    client = TestClient(app)
    client.__enter__()
    assert client.post("/airports/sync").status_code == 200
    return client
