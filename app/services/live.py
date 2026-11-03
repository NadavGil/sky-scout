import json
import math
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.config import Settings
from app.models import Airport, Observation, RawSnapshot
from app.services.catalog import scheme_value
from app.sources.opensky import OpenSkyClient, UpstreamError, interpret_flights, interpret_states
from app.sources.weather import fetch_report

OPENSKY_COVERAGE = (
    "OpenSky receiver sample for the requested window. Not annual passengers, seats, or movements."
)
TWO_DAYS = 2 * 24 * 60 * 60


def bounding_box(latitude: float, longitude: float, radius_km: float) -> tuple[float, float, float, float]:
    delta_lat = radius_km / 110.574
    cosine = math.cos(math.radians(latitude))
    delta_lon = radius_km / (111.320 * cosine) if abs(cosine) > 1e-6 else radius_km / 111.320
    return latitude - delta_lat, longitude - delta_lon, latitude + delta_lat, longitude + delta_lon


def yesterday_window(now: datetime | None = None) -> tuple[int, int]:
    moment = now or clock.utcnow()
    today = datetime(moment.year, moment.month, moment.day)
    start = today - timedelta(days=1)
    return int(start.timestamp()), int(today.timestamp())


def _latest(
    session: Session,
    airport_id: int,
    metric: str,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    match_period: bool = False,
) -> Observation | None:
    statement = select(Observation).where(Observation.airport_id == airport_id, Observation.metric == metric)
    if match_period:
        statement = statement.where(Observation.period_start == period_start, Observation.period_end == period_end)
    statement = statement.order_by(Observation.retrieved_at.desc(), Observation.id.desc())
    return session.scalars(statement).first()


def _fresh(observation: Observation | None, window: timedelta) -> bool:
    return observation is not None and observation.retrieved_at >= clock.utcnow() - window


def _snapshot(
    session: Session,
    airport: Airport,
    kind: str,
    params: dict,
    body: str,
    retrieved_at: datetime,
) -> RawSnapshot:
    snapshot = RawSnapshot(
        airport_id=airport.id,
        source="opensky" if kind != "weather" else "aviationweather",
        kind=kind,
        request_params=json.dumps(params),
        body=body,
        retrieved_at=retrieved_at,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def _observation(session: Session, **kwargs) -> Observation:
    observation = Observation(**kwargs)
    session.add(observation)
    session.flush()
    return observation


async def read_traffic(
    session: Session,
    opensky: OpenSkyClient,
    settings: Settings,
    airport: Airport,
    refresh: bool,
) -> tuple[Observation | None, str | None, bool]:
    if airport.latitude is None or airport.longitude is None:
        raise UpstreamError("Airport has no coordinates")
    existing = _latest(session, airport.id, "aircraft_in_bbox")
    window = timedelta(seconds=settings.traffic_cache_seconds)
    if not refresh and _fresh(existing, window):
        return existing, None, True
    south, west, north, east = bounding_box(airport.latitude, airport.longitude, settings.traffic_radius_km)
    try:
        response = await opensky.states(south, west, north, east)
    except UpstreamError as exc:
        if existing is not None:
            return existing, exc.message, False
        raise
    if response.status_code != 200:
        error = UpstreamError(f"HTTP {response.status_code}")
        if existing is not None:
            return existing, error.message, False
        raise error
    retrieved_at = clock.utcnow()
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise UpstreamError("OpenSky response was not JSON") from exc
    value, flag = interpret_states(payload)
    published = None
    if isinstance(payload, dict) and isinstance(payload.get("time"), (int, float)):
        published = datetime.fromtimestamp(payload["time"])
    snapshot = _snapshot(
        session,
        airport,
        "traffic",
        {"lamin": south, "lomin": west, "lamax": north, "lomax": east},
        response.text,
        retrieved_at,
    )
    observation = _observation(
        session,
        airport_id=airport.id,
        snapshot_id=snapshot.id,
        metric="aircraft_in_bbox",
        value=value,
        unit="aircraft",
        period_start=published or retrieved_at,
        period_end=published or retrieved_at,
        source="opensky",
        retrieved_at=retrieved_at,
        published_at=published,
        value_kind="measured",
        quality_flags=json.dumps([] if flag == "ok" else [flag]),
        coverage_note=OPENSKY_COVERAGE,
        detail=None,
    )
    session.commit()
    return observation, None, False


async def read_flights(
    session: Session,
    opensky: OpenSkyClient,
    airport: Airport,
    kind: str,
    begin: int,
    end: int,
    refresh: bool,
) -> tuple[Observation | None, str | None, bool]:
    if end < begin:
        raise UpstreamError("end must be after begin")
    if end - begin > TWO_DAYS:
        raise UpstreamError("Time span must not exceed 2 days")
    icao = scheme_value(airport, "icao")
    if not icao:
        raise UpstreamError("This airport has no ICAO code")
    period_start = datetime.fromtimestamp(begin)
    period_end = datetime.fromtimestamp(end)
    metric = "arrival_flights" if kind == "arrival" else "departure_flights"
    existing = _latest(session, airport.id, metric, period_start, period_end, match_period=True)
    if not refresh and _fresh(existing, timedelta(hours=24)):
        return existing, None, True
    try:
        response = await opensky.flights(kind, icao, begin, end)
        value, flag, ok = interpret_flights(response)
    except UpstreamError as exc:
        if existing is not None:
            return existing, exc.message, False
        raise
    if not ok:
        raise UpstreamError("OpenSky flight response was not usable")
    retrieved_at = clock.utcnow()
    snapshot = _snapshot(
        session,
        airport,
        kind,
        {"airport": icao, "begin": begin, "end": end},
        response.text,
        retrieved_at,
    )
    observation = _observation(
        session,
        airport_id=airport.id,
        snapshot_id=snapshot.id,
        metric=metric,
        value=value,
        unit="flights",
        period_start=period_start,
        period_end=period_end,
        source="opensky",
        retrieved_at=retrieved_at,
        published_at=None,
        value_kind="measured",
        quality_flags=json.dumps([] if flag == "ok" else [flag]),
        coverage_note=OPENSKY_COVERAGE,
        detail=None,
    )
    session.commit()
    return observation, None, False


async def read_weather(
    session: Session,
    http,
    settings: Settings,
    sleeper,
    airport: Airport,
    refresh: bool,
) -> tuple[dict, dict]:
    station = scheme_value(airport, "icao")
    if not station:
        raise UpstreamError("This airport has no ICAO code")
    metar = await _read_report(session, http, settings, sleeper, airport, "metar", station, timedelta(minutes=60), refresh)
    taf = await _read_report(session, http, settings, sleeper, airport, "taf", station, timedelta(hours=6), refresh)
    return metar, taf


async def _read_report(
    session: Session,
    http,
    settings: Settings,
    sleeper,
    airport: Airport,
    product: str,
    station: str,
    window: timedelta,
    refresh: bool,
) -> dict:
    existing = _latest(session, airport.id, product)
    if not refresh and _fresh(existing, window):
        return _weather_payload(existing, None, True)
    try:
        response = await fetch_report(http, settings, sleeper, product, station)
    except UpstreamError as exc:
        if existing is not None:
            return _weather_payload(existing, exc.message, False)
        raise
    if response.status_code != 200:
        error = f"HTTP {response.status_code}"
        if existing is not None:
            return _weather_payload(existing, error, False)
        raise UpstreamError(error)
    retrieved_at = clock.utcnow()
    observation = _observation(
        session,
        airport_id=airport.id,
        snapshot_id=None,
        metric=product,
        value=None,
        unit="report",
        period_start=retrieved_at,
        period_end=retrieved_at,
        source="aviationweather",
        retrieved_at=retrieved_at,
        published_at=None,
        value_kind="measured",
        quality_flags=json.dumps(["context_only"]),
        coverage_note="Context only. Not used for demand, capacity, or profitability.",
        detail=response.text,
    )
    session.commit()
    return _weather_payload(observation, None, False)


def _weather_payload(observation: Observation | None, refresh_error: str | None, cached: bool) -> dict:
    body = None
    if observation and observation.detail:
        try:
            body = json.loads(observation.detail)
        except json.JSONDecodeError:
            body = observation.detail
    return {
        "observation": None if observation is None else serialize_observation(observation),
        "body": body,
        "refresh_error": refresh_error,
        "cached": cached,
    }


def serialize_observation(observation: Observation) -> dict:
    return {
        "id": observation.id,
        "metric": observation.metric,
        "value": observation.value,
        "unit": observation.unit,
        "period_start": _iso(observation.period_start),
        "period_end": _iso(observation.period_end),
        "source": observation.source,
        "retrieved_at": _iso(observation.retrieved_at),
        "published_at": _iso(observation.published_at),
        "value_kind": observation.value_kind,
        "quality_flags": json.loads(observation.quality_flags or "[]"),
        "coverage_note": observation.coverage_note,
    }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat() + "Z"
