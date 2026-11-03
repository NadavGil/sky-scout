import json

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select

from app.models import Airport, DatasetSync, Observation, RawSnapshot
from app.services.catalog import lookup_airport, preferred_scheme, search_airports
from app.services.live import read_flights, read_traffic, read_weather, serialize_observation, yesterday_window
from app.sources.opensky import UpstreamError

router = APIRouter()

FLIGHT_CREDENTIALS_DETAIL = "OpenSky flight credentials are not configured"


def _session(request: Request):
    return request.app.state.session_factory()


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() + "Z"


def _airport_payload(airport: Airport, matched_scheme: str | None = None) -> dict:
    return {
        "id": airport.id,
        "name": airport.name,
        "airport_type": airport.airport_type,
        "latitude": airport.latitude,
        "longitude": airport.longitude,
        "elevation_ft": airport.elevation_ft,
        "iso_country": airport.iso_country,
        "iso_region": airport.iso_region,
        "municipality": airport.municipality,
        "scheduled_service": airport.scheduled_service,
        "matched_scheme": matched_scheme,
        "identifiers": [
            {
                "scheme": item.scheme,
                "value": item.value,
                "source": item.source,
                "provenance": item.provenance,
                "ambiguous": item.ambiguous,
            }
            for item in sorted(airport.identifiers, key=lambda item: (item.scheme, item.value))
        ],
    }


def _require_airport(session, code: str) -> Airport:
    status, airport, matches = lookup_airport(session, code)
    if status == "missing":
        raise HTTPException(status_code=404, detail="Airport not found")
    if status == "ambiguous":
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Ambiguous identifier",
                "matches": [{"id": item.id, "name": item.name} for item in matches],
            },
        )
    return airport


def _datasets_state(app) -> str:
    task = getattr(app.state, "dataset_task", None)
    if task is not None and not task.done():
        return "loading"
    return "ready"


@router.get("/health")
def health(request: Request):
    session = _session(request)
    try:
        last = session.scalar(
            select(func.max(DatasetSync.fetched_at)).where(DatasetSync.source_url.contains("airports.csv"))
        )
        settings = request.app.state.settings
        return {
            "status": "ok",
            "last_airport_sync_at": _iso(last),
            "opensky_flight_credentials_configured": settings.opensky_flights_configured,
            "gemini_configured": bool(settings.gemini_api_key),
            "allow_project_costs_to_model": settings.allow_project_costs_to_model,
            "datasets": _datasets_state(request.app),
        }
    finally:
        session.close()


@router.post("/airports/sync")
async def sync_airports(request: Request):
    try:
        sync = await request.app.state.run_sync()
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    return {
        "row_count": sync["row_count"],
        "fetched_at": _iso(sync["fetched_at"]),
        "source_url": sync["source_url"],
    }


@router.get("/airports")
def list_airports(
    request: Request,
    q: str | None = None,
    country: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
):
    session = _session(request)
    try:
        airports = search_airports(session, q, country, limit)
        return [_airport_payload(airport) for airport in airports]
    finally:
        session.close()


@router.get("/airports/{code}")
def get_airport(code: str, request: Request):
    session = _session(request)
    try:
        airport = _require_airport(session, code)
        return _airport_payload(airport, preferred_scheme(airport, code))
    finally:
        session.close()


@router.get("/airports/{code}/traffic")
async def airport_traffic(code: str, request: Request, refresh: bool = False):
    session = _session(request)
    try:
        airport = _require_airport(session, code)
        try:
            observation, error, cached = await read_traffic(
                session, request.app.state.opensky, request.app.state.settings, airport, refresh
            )
        except UpstreamError as exc:
            raise HTTPException(status_code=502, detail=exc.message) from exc
        return {
            "observation": None if observation is None else serialize_observation(observation),
            "refresh_error": error,
            "cached": cached,
        }
    finally:
        session.close()


@router.get("/airports/{code}/arrivals")
async def airport_arrivals(
    code: str,
    request: Request,
    begin: int | None = None,
    end: int | None = None,
    refresh: bool = False,
):
    return await _flights(code, request, "arrival", begin, end, refresh)


@router.get("/airports/{code}/departures")
async def airport_departures(
    code: str,
    request: Request,
    begin: int | None = None,
    end: int | None = None,
    refresh: bool = False,
):
    return await _flights(code, request, "departure", begin, end, refresh)


async def _flights(code: str, request: Request, kind: str, begin: int | None, end: int | None, refresh: bool):
    if not request.app.state.settings.opensky_flights_configured:
        raise HTTPException(status_code=503, detail=FLIGHT_CREDENTIALS_DETAIL)
    if (begin is None) != (end is None):
        raise HTTPException(status_code=400, detail="begin and end must be supplied together")
    if begin is None or end is None:
        begin, end = yesterday_window()
    session = _session(request)
    try:
        airport = _require_airport(session, code)
        try:
            observation, error, cached = await read_flights(
                session, request.app.state.opensky, airport, kind, begin, end, refresh
            )
        except UpstreamError as exc:
            status = 400 if exc.message in {"Time span must not exceed 2 days", "end must be after begin", "This airport has no ICAO code"} else 502
            raise HTTPException(status_code=status, detail=exc.message) from exc
        return {
            "observation": None if observation is None else serialize_observation(observation),
            "refresh_error": error,
            "cached": cached,
        }
    finally:
        session.close()


@router.get("/airports/{code}/weather")
async def airport_weather(code: str, request: Request, refresh: bool = False):
    session = _session(request)
    try:
        airport = _require_airport(session, code)
        try:
            metar, taf = await read_weather(
                session,
                request.app.state.http,
                request.app.state.settings,
                request.app.state.sleeper,
                airport,
                refresh,
            )
        except UpstreamError as exc:
            status = 400 if exc.message == "This airport has no ICAO code" else 502
            raise HTTPException(status_code=status, detail=exc.message) from exc
        return {"metar": metar, "taf": taf}
    finally:
        session.close()


@router.get("/airports/{code}/observations")
def airport_observations(code: str, request: Request, metric: str | None = None):
    session = _session(request)
    try:
        airport = _require_airport(session, code)
        statement = select(Observation).where(Observation.airport_id == airport.id)
        if metric:
            statement = statement.where(Observation.metric == metric)
        statement = statement.order_by(Observation.retrieved_at.desc(), Observation.id.desc())
        rows = session.scalars(statement).all()
        return [serialize_observation(row) for row in rows]
    finally:
        session.close()


@router.get("/airports/{code}/snapshots")
def airport_snapshots(code: str, request: Request, kind: str | None = None):
    session = _session(request)
    try:
        airport = _require_airport(session, code)
        statement = select(RawSnapshot).where(RawSnapshot.airport_id == airport.id)
        if kind:
            statement = statement.where(RawSnapshot.kind == kind)
        statement = statement.order_by(RawSnapshot.retrieved_at.desc(), RawSnapshot.id.desc())
        rows = session.scalars(statement).all()
        return [
            {
                "id": row.id,
                "kind": row.kind,
                "source": row.source,
                "request_params": json.loads(row.request_params),
                "body": row.body,
                "retrieved_at": _iso(row.retrieved_at),
            }
            for row in rows
        ]
    finally:
        session.close()


