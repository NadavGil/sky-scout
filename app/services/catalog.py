import csv
import io
import logging
import re
from datetime import timedelta

import httpx
from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.orm import Session, selectinload

logger = logging.getLogger("sky_scout")

from app import clock
from app.config import Settings
from app.http import Sleeper, send
from app.models import Airport, AirportIdentifier, DatasetSync
from app.sources.opensky import UpstreamError

ICAO_PATTERN = re.compile(r"^[A-Z]{4}$")
SCHEME_PRIORITY = ("icao", "iata", "faa_lid", "gps", "ourairports_ident")

PROVENANCE = {
    "ourairports_ident": "OurAirports ident column. Not assumed to be ICAO.",
    "icao": "OurAirports gps_code. Stored as ICAO because it is four letters.",
    "iata": "OurAirports iata_code.",
    "faa_lid": "OurAirports local_code for a US airport. Not treated as IATA or ICAO.",
    "gps": "OurAirports gps_code.",
}


def _blank(value: str | None) -> str:
    return (value or "").strip()


def _upper(value: str | None) -> str:
    return _blank(value).upper()


def _float(value: str | None) -> float | None:
    text = _blank(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def startup_action(session: Session) -> str:
    count = session.scalar(select(func.count(Airport.id))) or 0
    if count == 0:
        return "immediate"
    last = session.scalar(
        select(func.max(DatasetSync.fetched_at)).where(DatasetSync.source_url.contains("airports.csv"))
    )
    if last is None or last < clock.utcnow() - timedelta(hours=24):
        return "background"
    return "skip"


async def download_catalog(http: httpx.AsyncClient, settings: Settings, sleeper: Sleeper) -> str:
    try:
        response = await send(http, "GET", settings.ourairports_url, "ourairports", sleeper)
    except httpx.TimeoutException as exc:
        raise UpstreamError("timeout") from exc
    if response.status_code != 200 or not response.text.strip():
        raise UpstreamError(f"HTTP {response.status_code}")
    return response.text


def import_catalog(session: Session, csv_text: str, source_url: str) -> DatasetSync:
    parsed = _parsed_rows(csv_text)
    logger.info("source=ourairports status=importing rows=%s", len(parsed))
    existing = dict(
        session.execute(
            select(AirportIdentifier.value, Airport.id)
            .join(Airport, Airport.id == AirportIdentifier.airport_id)
            .where(AirportIdentifier.scheme == "ourairports_ident")
        ).all()
    )
    fresh: list[tuple[str, dict]] = []
    updates: list[tuple[int, str, dict]] = []
    for ident, row in parsed.items():
        airport_id = existing.get(ident)
        if airport_id is None:
            fresh.append((ident, row))
        else:
            updates.append((airport_id, ident, row))

    if updates:
        airports: dict[int, Airport] = {}
        for airport_ids in _chunks([item[0] for item in updates]):
            for airport in session.scalars(select(Airport).where(Airport.id.in_(airport_ids))):
                airports[airport.id] = airport
        for airport_id, ident, row in updates:
            _apply_airport(airports[airport_id], row, ident)
        for airport_ids in _chunks([item[0] for item in updates]):
            session.execute(delete(AirportIdentifier).where(AirportIdentifier.airport_id.in_(airport_ids)))

    identifier_rows: list[dict] = []
    for airport_id, ident, row in updates:
        identifier_rows.extend(_identifier_rows(airport_id, _airport_values(row, ident)["iso_country"], row, ident))

    for offset in range(0, len(fresh), 1000):
        chunk = fresh[offset : offset + 1000]
        inserted = session.execute(
            insert(Airport).returning(Airport.id),
            [_airport_values(row, ident) for ident, row in chunk],
        ).scalars().all()
        for airport_id, (ident, row) in zip(inserted, chunk, strict=True):
            identifier_rows.extend(_identifier_rows(airport_id, _airport_values(row, ident)["iso_country"], row, ident))

    for identifier_chunk in _chunks(identifier_rows, 1000):
        session.execute(insert(AirportIdentifier), identifier_chunk)
    session.flush()
    _recompute_ambiguous(session)
    sync = DatasetSync(source_url=source_url, row_count=len(parsed), fetched_at=clock.utcnow())
    session.add(sync)
    session.commit()
    logger.info("source=ourairports status=imported rows=%s", len(parsed))
    return sync


def _parsed_rows(csv_text: str) -> dict[str, dict]:
    parsed: dict[str, dict] = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        ident = _upper(row.get("ident"))
        if ident:
            parsed[ident] = row
    return parsed


def _airport_values(row: dict, ident: str) -> dict:
    return {
        "name": _blank(row.get("name")) or ident,
        "airport_type": _blank(row.get("type")) or None,
        "latitude": _float(row.get("latitude_deg")),
        "longitude": _float(row.get("longitude_deg")),
        "elevation_ft": _float(row.get("elevation_ft")),
        "iso_country": _upper(row.get("iso_country")) or None,
        "iso_region": _blank(row.get("iso_region")) or None,
        "municipality": _blank(row.get("municipality")) or None,
        "scheduled_service": _blank(row.get("scheduled_service")) or None,
    }


def _apply_airport(airport: Airport, row: dict, ident: str) -> None:
    for field, value in _airport_values(row, ident).items():
        setattr(airport, field, value)


def _identifier_rows(airport_id: int, iso_country: str | None, row: dict, ident: str) -> list[dict]:
    codes: list[tuple[str, str]] = [("ourairports_ident", ident)]
    gps = _upper(row.get("gps_code"))
    iata = _upper(row.get("iata_code"))
    local = _upper(row.get("local_code"))
    if gps:
        codes.append(("gps", gps))
        if ICAO_PATTERN.fullmatch(gps):
            codes.append(("icao", gps))
    if iata:
        codes.append(("iata", iata))
    if local and iso_country == "US":
        codes.append(("faa_lid", local))
    seen: set[tuple[str, str]] = set()
    rows: list[dict] = []
    for scheme, value in codes:
        if (scheme, value) in seen:
            continue
        seen.add((scheme, value))
        rows.append(
            {
                "airport_id": airport_id,
                "scheme": scheme,
                "value": value,
                "source": "ourairports",
                "provenance": PROVENANCE[scheme],
                "ambiguous": False,
            }
        )
    return rows


def _chunks(items: list, size: int = 500) -> list[list]:
    return [items[offset : offset + size] for offset in range(0, len(items), size)]


def _recompute_ambiguous(session: Session) -> None:
    session.execute(update(AirportIdentifier).values(ambiguous=False))
    session.flush()
    duplicates = session.execute(
        select(AirportIdentifier.scheme, AirportIdentifier.value)
        .group_by(AirportIdentifier.scheme, AirportIdentifier.value)
        .having(func.count(func.distinct(AirportIdentifier.airport_id)) > 1)
    ).all()
    for scheme, value in duplicates:
        session.execute(
            update(AirportIdentifier)
            .where(AirportIdentifier.scheme == scheme, AirportIdentifier.value == value)
            .values(ambiguous=True)
        )


def search_airports(session: Session, q: str | None, country: str | None, limit: int) -> list[Airport]:
    statement = select(Airport).options(selectinload(Airport.identifiers))
    if country:
        statement = statement.where(func.upper(Airport.iso_country) == country.strip().upper())
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        ident_ids = select(AirportIdentifier.airport_id).where(func.lower(AirportIdentifier.value).like(like))
        statement = statement.where(
            or_(
                func.lower(Airport.name).like(like),
                func.lower(func.coalesce(Airport.municipality, "")).like(like),
                Airport.id.in_(ident_ids.scalar_subquery()),
            )
        )
    statement = statement.order_by(Airport.name).limit(limit)
    return list(session.scalars(statement).unique().all())


def lookup_airport(session: Session, code: str) -> tuple[str, Airport | None, list[Airport]]:
    normalized = code.strip().upper()
    if not normalized:
        return "missing", None, []
    rows = list(
        session.scalars(
            select(AirportIdentifier).where(func.upper(AirportIdentifier.value) == normalized)
        ).all()
    )
    if not rows:
        return "missing", None, []
    airport_ids = {row.airport_id for row in rows}
    airports = list(
        session.scalars(
            select(Airport).options(selectinload(Airport.identifiers)).where(Airport.id.in_(airport_ids))
        ).all()
    )
    if len(airports) > 1:
        return "ambiguous", None, airports
    return "found", airports[0], []


def preferred_scheme(airport: Airport, code: str) -> str:
    normalized = code.strip().upper()
    matched = {item.scheme for item in airport.identifiers if item.value.upper() == normalized}
    for scheme in SCHEME_PRIORITY:
        if scheme in matched:
            return scheme
    return sorted(matched)[0]


def scheme_value(airport: Airport, scheme: str) -> str | None:
    for item in airport.identifiers:
        if item.scheme == scheme:
            return item.value
    return None
