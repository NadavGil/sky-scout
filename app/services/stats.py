import csv
import hashlib
import io
import json
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import clock
from app.config import Settings
from app.http import Sleeper, send
from app.models import Airport, AirportIdentifier, DatasetSync, Observation, RawSnapshot
from app.scoring.geography import is_territory
from app.sources.opensky import UpstreamError

FAA_COVERAGE = (
    "FAA annual passenger boardings (enplanements). Not total terminal passengers, seats, or movements. "
    "U.S. territories are excluded."
)
T100_COVERAGE = (
    "BTS T-100 segment departures performed. Distance is published great-circle miles. "
    "A blank distance is an excluded destination, not a short-haul flight. Not passengers or seats."
)
ONTIME_COVERAGE = (
    "BTS reporting-carrier on-time performance for arrivals at this airport. "
    "Share is arrivals delayed at least 15 minutes over operated arrivals. Not a live aircraft count."
)
YEAR_HEADER = re.compile(r"cy\s*(\d{2,4})")
logger = logging.getLogger("sky_scout")


async def sync_faa_enplanements(session: Session, http: httpx.AsyncClient, settings: Settings, sleeper: Sleeper) -> dict:
    imported = []
    errors = []
    skipped = []
    for year in settings.faa_years:
        url = settings.faa_enplanements_url(year)
        if _already_loaded(session, url):
            skipped.append(year)
            logger.info("source=faa status=present year=%s", year)
            continue
        logger.info("source=faa status=loading year=%s", year)
        try:
            content = await _download(http, url, "faa", settings, sleeper)
            imported.append(import_faa_xlsx(session, content, url))
        except (UpstreamError, ValueError) as exc:
            errors.append({"year": year, "error": str(exc)})
            logger.info("source=faa status=error year=%s", year)
    if not imported and not skipped:
        raise UpstreamError(errors[0]["error"] if errors else "FAA enplanements were not imported")
    return {"imports": imported, "errors": errors, "skipped": skipped}


def t100_form_fields(html: str, year: int) -> dict[str, str]:
    fields = {
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "__LASTFOCUS": "",
        "txtSearch": "",
        "btnDownload": "Download",
        "cboGeography": "All",
        "cboYear": str(year),
        "cboPeriod": "All",
        "ORIGIN": "on",
        "DEST": "on",
        "DISTANCE": "on",
        "DEPARTURES_PERFORMED": "on",
        "YEAR": "on",
        "MONTH": "on",
    }
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        match = re.search(rf'id="{name}"[^>]*value="([^"]*)"', html)
        if not match:
            raise UpstreamError("BTS T-100 download form was not recognized")
        fields[name] = match.group(1)
    return fields


def t100_source_url(settings: Settings, year: int) -> str:
    return f"{settings.bts_t100_form_url}&year={year}"


async def download_t100_year(http: httpx.AsyncClient, settings: Settings, sleeper: Sleeper, year: int) -> bytes:
    page = await send(http, "GET", settings.bts_t100_form_url, "bts_t100", sleeper, timeout=60)
    if page.status_code != 200 or not page.text:
        raise UpstreamError(f"HTTP {page.status_code}")
    response = await send(
        http,
        "POST",
        settings.bts_t100_form_url,
        "bts_t100",
        sleeper,
        data=t100_form_fields(page.text, year),
        headers={"referer": settings.bts_t100_form_url},
        timeout=settings.dataset_timeout_seconds,
    )
    if response.status_code != 200 or not response.content.startswith(b"PK"):
        raise UpstreamError(f"HTTP {response.status_code}")
    return response.content


async def sync_t100(
    session: Session,
    http: httpx.AsyncClient,
    settings: Settings,
    sleeper: Sleeper,
    year: int,
    month: int,
) -> dict:
    url = settings.bts_t100_url_template.format(year=year, month=month)
    content = await _download(http, url, "bts_t100", settings, sleeper)
    return import_t100_csv(session, _csv_from_download(content), url)


async def sync_ontime(
    session: Session,
    http: httpx.AsyncClient,
    settings: Settings,
    sleeper: Sleeper,
    year: int,
    month: int,
) -> dict:
    url = settings.bts_ontime_url_template.format(year=year, month=month)
    content = await _download(http, url, "bts_ontime", settings, sleeper)
    return import_ontime_bytes(session, content, url)


def import_faa_csv(session: Session, text: str, source_url: str) -> dict:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise UpstreamError("FAA file has no header")
    headers = _headers(reader.fieldnames)
    if "year" in headers and _enplanement_column(headers):
        records = _long_enplanements(reader, headers)
    else:
        records = _wide_enplanements(text)
    return _store_enplanements(session, records, source_url, text.encode())


def import_faa_xlsx(session: Session, content: bytes, source_url: str) -> dict:
    rows = _xlsx_rows(content)
    header_index = None
    for index, row in enumerate(rows[:20]):
        labels = [_text(cell).lower() for cell in row]
        if "locid" in labels:
            header_index = index
            break
    if header_index is None:
        raise UpstreamError("FAA workbook has no Locid column")
    header = [_text(cell) for cell in rows[header_index]]
    year_columns = [(column, year) for column, year in _year_columns(header)]
    state_column = _find(header, "st")
    loc_column = _find(header, "locid")
    records = []
    for row in rows[header_index + 1 :]:
        locid = _text(row[loc_column] if loc_column < len(row) else "")
        state = _text(row[state_column] if state_column is not None and state_column < len(row) else "")
        if not locid:
            continue
        for column, year in year_columns:
            if column >= len(row):
                continue
            value = _number(row[column])
            if value is None:
                continue
            records.append((locid, state, year, value))
    return _store_enplanements(session, records, source_url, content)


def import_t100_csv(session: Session, text: str, source_url: str) -> dict:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise UpstreamError("T-100 file has no header")
    headers = _headers(reader.fieldnames)
    index = _code_index(session)
    totals: dict[tuple[int, int, int], dict] = {}
    unmapped = 0
    skipped_territory = 0
    for row in reader:
        origin = _cell(row, headers, "origin").upper()
        departures = _number(_cell(row, headers, "departures_performed"))
        if not origin or departures is None:
            continue
        year = _integer(_cell(row, headers, "year"))
        month = _integer(_cell(row, headers, "month"))
        if year is None or month is None:
            continue
        matched = _match(index, origin, ("iata", "faa_lid"))
        if matched == "territory":
            skipped_territory += 1
            continue
        if matched is None:
            unmapped += 1
            continue
        airport_id, scheme = matched
        bucket = totals.setdefault(
            (airport_id, year, month),
            {"long": 0.0, "short": 0.0, "unknown": 0.0, "destinations": set(), "scheme": scheme},
        )
        distance = _number(_cell(row, headers, "distance"))
        if distance is None:
            bucket["unknown"] += departures
            destination = _cell(row, headers, "dest").upper() or "(blank)"
            bucket["destinations"].add(destination)
        elif distance >= 3000:
            bucket["long"] += departures
        else:
            bucket["short"] += departures
    periods = {}
    for airport_id, year, month in totals:
        periods[(airport_id, year, month)] = _month_bounds(year, month)
    _clear(
        session,
        "bts_t100",
        ("long_haul_departures", "geocoded_departures", "unknown_destination_departures"),
        [start for start, _end in periods.values()],
    )
    written = 0
    for (airport_id, year, month), bucket in totals.items():
        start, end = periods[(airport_id, year, month)]
        geocoded = bucket["long"] + bucket["short"]
        _put(session, airport_id, "long_haul_departures", bucket["long"], "flights", start, end, "bts_t100", T100_COVERAGE, None)
        _put(session, airport_id, "geocoded_departures", geocoded, "flights", start, end, "bts_t100", T100_COVERAGE, None)
        _put(
            session,
            airport_id,
            "unknown_destination_departures",
            bucket["unknown"],
            "flights",
            start,
            end,
            "bts_t100",
            T100_COVERAGE,
            json.dumps({"destinations": sorted(bucket["destinations"]), "matched_scheme": bucket["scheme"]}),
        )
        written += 3
    _finish(session, source_url, text.encode(), "bts_t100", written)
    return {"row_count": written, "airports": len(totals), "unmapped": unmapped, "skipped_territory": skipped_territory}


def import_ontime_csv(session: Session, text: str, source_url: str) -> dict:
    return _import_ontime(session, csv.reader(io.StringIO(text)), source_url, text.encode())


def import_ontime_bytes(session: Session, content: bytes, source_url: str) -> dict:
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not names:
                raise UpstreamError("The BTS download contained no CSV")
            with archive.open(names[0]) as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
                return _import_ontime(session, reader, source_url, content)
    return _import_ontime(session, csv.reader(io.StringIO(content.decode("utf-8-sig", errors="replace"))), source_url, content)


def _import_ontime(session: Session, reader, source_url: str, content: bytes) -> dict:
    try:
        header = next(reader)
    except StopIteration as exc:
        raise UpstreamError("On-time file has no header") from exc
    if not any(_text(cell) for cell in header):
        raise UpstreamError("On-time file has no header")
    columns = {_text(name).lower(): index for index, name in enumerate(header)}
    cancelled_at = columns.get("cancelled")
    delayed_at = columns.get("arrdel15", columns.get("arr_del15"))
    dest_at = columns.get("dest")
    year_at = columns.get("year")
    month_at = columns.get("month")
    minutes_at = columns.get("arrdelay", columns.get("arr_delay"))
    index = _code_index(session)
    totals: dict[tuple[int, int, int], dict] = {}
    unmapped = 0
    skipped_territory = 0
    for row in reader:
        cancelled = _number(_at(row, cancelled_at))
        if cancelled == 1:
            continue
        delayed = _number(_at(row, delayed_at))
        if delayed not in (0, 1):
            continue
        dest = _at(row, dest_at).upper()
        year = _integer(_at(row, year_at))
        month = _integer(_at(row, month_at))
        if not dest or year is None or month is None:
            continue
        matched = _match(index, dest, ("iata", "faa_lid"))
        if matched == "territory":
            skipped_territory += 1
            continue
        if matched is None:
            unmapped += 1
            continue
        airport_id, _scheme = matched
        bucket = totals.setdefault((airport_id, year, month), {"delayed": 0, "operated": 0, "delay_sum": 0.0, "delay_count": 0})
        bucket["operated"] += 1
        bucket["delayed"] += int(delayed)
        minutes = _number(_at(row, minutes_at))
        if minutes is not None:
            bucket["delay_sum"] += minutes
            bucket["delay_count"] += 1
    periods = {}
    for airport_id, year, month in totals:
        if totals[(airport_id, year, month)]["operated"]:
            periods[(airport_id, year, month)] = _month_bounds(year, month)
    _clear(session, "bts_ontime", ("arrival_delay_share", "arrival_delay_mean_minutes"), [start for start, _end in periods.values()])
    written = 0
    for (airport_id, year, month), bucket in totals.items():
        if (airport_id, year, month) not in periods:
            continue
        start, end = periods[(airport_id, year, month)]
        share = bucket["delayed"] / bucket["operated"]
        _put(
            session,
            airport_id,
            "arrival_delay_share",
            share,
            "share",
            start,
            end,
            "bts_ontime",
            ONTIME_COVERAGE,
            json.dumps({"delayed": bucket["delayed"], "operated": bucket["operated"]}),
        )
        written += 1
        if bucket["delay_count"]:
            _put(
                session,
                airport_id,
                "arrival_delay_mean_minutes",
                bucket["delay_sum"] / bucket["delay_count"],
                "minutes",
                start,
                end,
                "bts_ontime",
                ONTIME_COVERAGE,
                None,
            )
            written += 1
    _finish(session, source_url, content, "bts_ontime", written)
    return {"row_count": written, "airports": len(totals), "unmapped": unmapped, "skipped_territory": skipped_territory}


def _store_enplanements(session: Session, records: list[tuple[str, str, int, float]], source_url: str, content: bytes) -> dict:
    index = _code_index(session)
    accepted = []
    unmapped = 0
    skipped_territory = 0
    for locid, state, year, value in records:
        if state.upper() in {"AS", "GU", "MP", "PR", "VI"}:
            skipped_territory += 1
            continue
        matched = _match(index, locid, ("faa_lid",))
        if matched == "territory":
            skipped_territory += 1
            continue
        if matched is None:
            unmapped += 1
            continue
        accepted.append((matched[0], year, value))
    _clear(session, "faa_enplanements", ("enplanements",), [datetime(year, 1, 1) for _airport_id, year, _value in accepted])
    written = 0
    for airport_id, year, value in accepted:
        _put(
            session,
            airport_id,
            "enplanements",
            value,
            "passengers",
            datetime(year, 1, 1),
            datetime(year + 1, 1, 1),
            "faa_enplanements",
            FAA_COVERAGE,
            None,
        )
        written += 1
    _finish(session, source_url, content, "faa_enplanements", written)
    return {"row_count": written, "unmapped": unmapped, "skipped_territory": skipped_territory, "source_url": source_url}


def _put(session, airport_id, metric, value, unit, start, end, source, coverage, detail) -> None:
    session.add(
        Observation(
            airport_id=airport_id,
            snapshot_id=None,
            metric=metric,
            value=value,
            unit=unit,
            period_start=start,
            period_end=end,
            source=source,
            retrieved_at=clock.utcnow(),
            published_at=None,
            value_kind="measured",
            quality_flags="[]",
            coverage_note=coverage,
            detail=detail,
        )
    )


def _clear(session: Session, source: str, metrics: tuple[str, ...], starts: list[datetime]) -> None:
    unique = list({start for start in starts})
    if not unique:
        return
    session.execute(
        delete(Observation).where(
            Observation.source == source,
            Observation.metric.in_(metrics),
            Observation.period_start.in_(unique),
        )
    )


def _finish(session: Session, source_url: str, content: bytes, kind: str, row_count: int) -> None:
    retrieved_at = clock.utcnow()
    session.add(
        RawSnapshot(
            airport_id=None,
            source=kind,
            kind=kind,
            request_params=json.dumps({"source_url": source_url}),
            body=json.dumps({"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content), "stored_body": False}),
            retrieved_at=retrieved_at,
        )
    )
    session.add(DatasetSync(source_url=source_url, row_count=row_count, fetched_at=retrieved_at))
    session.commit()


def _code_index(session: Session) -> dict[tuple[str, str], list[tuple]]:
    rows = session.execute(
        select(
            AirportIdentifier.scheme,
            AirportIdentifier.value,
            AirportIdentifier.airport_id,
            AirportIdentifier.ambiguous,
            Airport.iso_country,
            Airport.iso_region,
        ).join(Airport, Airport.id == AirportIdentifier.airport_id)
    ).all()
    index: dict[tuple[str, str], list[tuple]] = {}
    for scheme, value, airport_id, ambiguous, country, region in rows:
        index.setdefault((scheme, value.upper()), []).append((airport_id, ambiguous, country, region))
    return index


def _match(index: dict, code: str, schemes: tuple[str, ...]):
    hits = []
    for scheme in schemes:
        entries = [entry for entry in index.get((scheme, code.upper()), []) if not entry[1]]
        airport_ids = {entry[0] for entry in entries}
        if len(airport_ids) > 1:
            return None
        if len(airport_ids) == 1:
            hits.append((entries[0], scheme))
    if not hits:
        return None
    if len({hit[0][0] for hit in hits}) != 1:
        return None
    entry, scheme = hits[0]
    _airport_id, _ambiguous, country, region = entry
    if country != "US" or is_territory(region):
        return "territory"
    return entry[0], scheme


def _long_enplanements(reader: csv.DictReader, headers: dict[str, str]) -> list[tuple[str, str, int, float]]:
    records = []
    column = _enplanement_column(headers)
    for row in reader:
        locid = _cell(row, headers, "faa_lid", "locid", "location_id").upper()
        year = _integer(_cell(row, headers, "year"))
        value = _number(_cell(row, headers, column))
        state = _cell(row, headers, "st", "state").upper()
        if locid and year is not None and value is not None:
            records.append((locid, state, year, value))
    return records


def _wide_enplanements(text: str) -> list[tuple[str, str, int, float]]:
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    header_index = None
    for index, row in enumerate(rows[:20]):
        if any(cell.strip().lower() == "locid" for cell in row):
            header_index = index
            break
    if header_index is None:
        raise UpstreamError("FAA file has no Locid column")
    header = rows[header_index]
    loc_column = _find(header, "locid")
    state_column = _find(header, "st")
    columns = _year_columns(header)
    records = []
    for row in rows[header_index + 1 :]:
        locid = _text(row[loc_column] if loc_column < len(row) else "")
        state = _text(row[state_column] if state_column is not None and state_column < len(row) else "")
        if not locid:
            continue
        for column, year in columns:
            if column >= len(row):
                continue
            value = _number(row[column])
            if value is not None:
                records.append((locid, state, year, value))
    return records


def _year_columns(header: list) -> list[tuple[int, int]]:
    columns = []
    for index, cell in enumerate(header):
        match = YEAR_HEADER.search(_text(cell).lower())
        if not match or "enplane" not in _text(cell).lower():
            continue
        year = int(match.group(1))
        if year < 100:
            year += 2000
        columns.append((index, year))
    return columns


def _enplanement_column(headers: dict[str, str]) -> str:
    for name in headers:
        if "enplane" in name or name == "passengers":
            return name
    return ""


def _headers(fieldnames: list[str]) -> dict[str, str]:
    return {name.strip().lower(): name for name in fieldnames if name}


def _cell(row: dict, headers: dict[str, str], *names: str) -> str:
    for name in names:
        original = headers.get(name.lower())
        if original is not None:
            return _text(row.get(original))
    return ""


def _find(header: list, name: str) -> int | None:
    for index, cell in enumerate(header):
        if _text(cell).lower() == name:
            return index
    return None


XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _xlsx_rows(content: bytes) -> list[list[str]]:
    namespace = XLSX_NS["m"]
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", XLSX_NS):
                shared.append("".join(node.text or "" for node in item.iter(f"{{{namespace}}}t")))
        sheets = [name for name in names if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")]
        if not sheets:
            raise UpstreamError("FAA workbook has no worksheet")
        root = ET.fromstring(archive.read(sorted(sheets)[0]))
    table = []
    for row in root.findall("m:sheetData/m:row", XLSX_NS):
        values = {}
        for cell in row.findall("m:c", XLSX_NS):
            ref = cell.attrib.get("r", "")
            index = _column_index(ref) if ref else len(values)
            kind = cell.attrib.get("t")
            if kind == "inlineStr":
                text = "".join(node.text or "" for node in cell.iter(f"{{{namespace}}}t"))
            elif kind == "s":
                pointer = cell.find("m:v", XLSX_NS)
                text = shared[int(pointer.text)] if pointer is not None and pointer.text else ""
            else:
                pointer = cell.find("m:v", XLSX_NS)
                text = pointer.text if pointer is not None and pointer.text else ""
            values[index] = text
        width = max(values) + 1 if values else 0
        table.append([values.get(cursor, "") for cursor in range(width)])
    return table


def _column_index(ref: str) -> int:
    letters = "".join(character for character in ref if character.isalpha())
    index = 0
    for character in letters:
        index = index * 26 + (ord(character.upper()) - 64)
    return index - 1


def _at(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return row[index]


def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _number(value) -> float | None:
    text = _text(value).replace(",", "").replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _integer(value) -> int | None:
    number = _number(value)
    if number is None:
        return None
    return int(number)


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start, end


async def ensure_question_data(http: httpx.AsyncClient, settings: Settings, sleeper: Sleeper, session_factory) -> None:
    from app.db import session_scope

    try:
        with session_scope(session_factory) as session:
            await sync_faa_enplanements(session, http, settings, sleeper)
    except (UpstreamError, httpx.HTTPError, ValueError):
        logger.exception("source=faa status=error")
    await _ensure_ontime(http, settings, sleeper, session_factory)
    await _ensure_t100(http, settings, sleeper, session_factory)
    logger.info("source=datasets status=ready")


async def _ensure_ontime(http, settings, sleeper, session_factory) -> None:
    from app.db import session_scope

    with session_scope(session_factory) as session:
        if _stored_ontime(session):
            logger.info("source=bts_ontime status=present")
            return
    year, month = await _latest_published_ontime_month(http, settings, sleeper)
    if year is None:
        logger.info("source=bts_ontime status=unpublished")
        return
    url = settings.bts_ontime_url_template.format(year=year, month=month)
    with session_scope(session_factory) as session:
        if _already_loaded(session, url):
            logger.info("source=bts_ontime status=present period=%s-%02d", year, month)
            return
    logger.info("source=bts_ontime status=loading period=%s-%02d", year, month)
    try:
        with session_scope(session_factory) as session:
            await sync_ontime(session, http, settings, sleeper, year, month)
    except (UpstreamError, httpx.HTTPError, ValueError):
        logger.exception("source=bts_ontime status=error")


async def _ensure_t100(http, settings, sleeper, session_factory) -> None:
    from app.db import session_scope

    year = clock.utcnow().year - 1
    url = t100_source_url(settings, year)
    with session_scope(session_factory) as session:
        if _already_loaded(session, url):
            logger.info("source=bts_t100 status=present year=%s", year)
            return
    logger.info("source=bts_t100 status=loading year=%s", year)
    try:
        content = await download_t100_year(http, settings, sleeper, year)
        with session_scope(session_factory) as session:
            imported = import_t100_csv(session, _csv_from_download(content), url)
        logger.info("source=bts_t100 status=stored year=%s rows=%s", year, imported["row_count"])
    except (UpstreamError, httpx.HTTPError, ValueError):
        logger.exception("source=bts_t100 status=error")


async def _latest_published_ontime_month(
    http: httpx.AsyncClient, settings: Settings, sleeper: Sleeper
) -> tuple[int | None, int | None]:
    year, month = _previous_complete_month()
    for _ in range(8):
        url = settings.bts_ontime_url_template.format(year=year, month=month)
        try:
            response = await send(http, "HEAD", url, "bts_ontime", sleeper, timeout=20)
        except (httpx.TimeoutException, httpx.RequestError):
            return None, None
        if response.status_code == 200:
            return year, month
        year, month = _previous_month(year, month)
    return None, None


def _previous_complete_month() -> tuple[int, int]:
    first = clock.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous = first - timedelta(days=1)
    return previous.year, previous.month


def _previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _stored_ontime(session: Session) -> bool:
    found = session.scalar(
        select(DatasetSync.id).where(DatasetSync.source_url.contains("On_Time_Reporting_Carrier")).limit(1)
    )
    return found is not None


def _already_loaded(session: Session, url: str) -> bool:
    found = session.scalar(select(DatasetSync.id).where(DatasetSync.source_url == url).limit(1))
    return found is not None


async def _download(http: httpx.AsyncClient, url: str, source: str, settings: Settings, sleeper: Sleeper) -> bytes:
    try:
        response = await send(http, "GET", url, source, sleeper, timeout=settings.dataset_timeout_seconds)
    except httpx.TimeoutException as exc:
        raise UpstreamError("timeout") from exc
    except httpx.RequestError as exc:
        raise UpstreamError("The download could not reach the source") from exc
    if response.status_code != 200 or not response.content:
        raise UpstreamError(f"HTTP {response.status_code}")
    return response.content


def _csv_from_download(content: bytes) -> str:
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not names:
                raise UpstreamError("The BTS download contained no CSV")
            content = archive.read(names[0])
    return content.decode("utf-8-sig", errors="replace")
