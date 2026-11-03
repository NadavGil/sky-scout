import json
import math
import re
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import clock
from app.models import Airport, Message, Observation, RawSnapshot
from app.scoring.evidence import list_evidence
from app.scoring.finance import capacity_mapping_score, cash_flow_schedule
from app.scoring.formulas import DEMAND_WEIGHTS, demand_scores, opportunity_score
from app.scoring.geography import (
    COMMERCIAL_TYPES,
    LONG_HAUL_MILES,
    PEER_SET,
    STATE_NAMES,
    TERRITORY_STATEMENT,
    is_territory,
    resolve_region,
)
from app.services.catalog import lookup_airport, scheme_value, search_airports

LA_ALIASES = {"la", "l.a.", "los angeles"}
SANTA_ANA_ALIASES = {"santa ana"}


def screen(session: Session, region: str, project_type: str | None = None, conversation_id: int | None = None) -> dict:
    result = _base(session, "screen", conversation_id)
    result["project_type"] = project_type
    result["query"] = region
    result["peer_set"] = PEER_SET
    result["ranked"] = []
    resolved = resolve_region(region or "")
    if resolved is None:
        result.update(
            status="unknown_region",
            region=None,
            states=[],
            state_names=[],
            peer_count=0,
            airports=[],
            reason="That region is not a U.S. state, Census division, or Census region.",
        )
        return result
    label, codes = resolved
    peers = _peers(session, codes)
    history = _enplanement_series(session, [airport.id for airport in peers])
    demand = demand_scores(history)
    ranked = []
    airports = []
    for airport in peers:
        item = _airport_item(airport)
        demand_score = demand["scores"].get(airport.id)
        capacity = _capacity_score(session, airport.id, result["evidence"])
        renovation, renovation_note = _renovation_fit(result["evidence"], airport.id)
        item["renovation_note"] = renovation_note
        if demand_score is None:
            item["classification"] = "insufficient_data"
            item["reason"] = demand["reason"] or "Six comparable annual enplanements are not on file."
            airports.append(item)
            continue
        item["demand_score"] = demand_score
        item["demand_parts"] = demand["parts"].get(airport.id)
        if capacity is None or renovation is None:
            item["classification"] = "demand_growth_watchlist"
            item["reason"] = (
                "Passenger history is on file. A capacity mapping and a relevant intervention are not both on file, "
                "so there is no opportunity score."
            )
            airports.append(item)
            continue
        score = opportunity_score(demand_score, capacity, renovation)
        item.update(
            classification="eligible",
            demand_score=demand_score,
            capacity_score=capacity,
            renovation_fit=renovation,
            opportunity_score=score,
            incremental_passengers="unknown",
            incremental_movements="unknown",
        )
        airports.append(item)
        ranked.append(item)
    ranked.sort(key=lambda item: item["opportunity_score"], reverse=True)
    status = "ranked" if ranked else "watchlist" if any("demand_score" in item for item in airports) else "insufficient_data"
    result.update(
        status=status,
        region=label,
        states=codes,
        state_names=[STATE_NAMES[code] for code in codes],
        peer_count=len(peers),
        airports=airports,
        ranked=ranked,
        demand_window=demand["window"],
        demand_cohort_size=len(demand["cohort"]),
        demand_weights=DEMAND_WEIGHTS,
        normalization="Ascending average rank within the cohort. The weakest peer scores 0, which would make S zero.",
        reason=_screen_reason(status),
        assumptions=[
            "Territory setting is states and the District of Columbia. Territories are excluded.",
            "Peer airports are scheduled commercial-service airports in the named states.",
            "Demand uses FAA enplanements only. Enplanements are not doubled and are not movements.",
            "Weights are provisional design assumptions, not validated investment coefficients.",
            "No reduced-data model is declared. Fewer than six annual observations means D is unavailable.",
            "A cohort smaller than two airports is not normalized.",
            "C is calculated only from a stored capacity-pressure value with a written mapping. Delays are not C.",
            "R is 50 only when this airport has analyst-supplied bottleneck and intervention text. That is not a documented 75 or 100.",
            "Robustness tests were not run.",
        ],
    )
    return result


def compare(
    session: Session,
    airports: list[str],
    metric: str,
    period: str | None = None,
    conversation_id: int | None = None,
    periods: list[str] | None = None,
) -> dict:
    result = _base(session, "compare", conversation_id)
    result["metric"] = metric
    result["substituted_live_traffic"] = False
    if periods:
        return _compare_separate_periods(session, result, airports, periods)
    if not period:
        period = _latest_ontime_label(session)
    start, end, label = _month_period(period)
    result["period"] = {"start": _iso(start), "end": _iso(end), "label": label}
    resolved = [_resolve_place(session, name) for name in airports]
    result["airports"] = [
        {
            "query": item["query"],
            "status": item["status"],
            "name": None if item.get("airport") is None else item["airport"].name,
            "id": None if item.get("airport") is None else item["airport"].id,
            "left_out": item.get("left_out", []),
            "matches": item.get("matches", []),
            "defaulted": item.get("defaulted", False),
        }
        for item in resolved
    ]
    if metric != "congestion":
        result["status"] = "unsupported_metric"
        result["reason"] = "This compare function scores BTS on-time arrival congestion only."
        return result
    if any(item["status"] != "found" for item in resolved):
        result["status"] = "unresolved"
        result["reason"] = "One or more airports were missing or ambiguous, so no comparison was calculated."
        return result
    values = []
    for item in resolved:
        airport = item["airport"]
        share = _metric_row(session, airport.id, "arrival_delay_share", start, end)
        mean = _metric_row(session, airport.id, "arrival_delay_mean_minutes", start, end)
        if share is None or mean is None or share.value is None or mean.value is None:
            result["status"] = "unavailable"
            result["values"] = None
            result["reason"] = (
                "BTS on-time arrival delay for this complete month is not loaded. "
                "A live aircraft count is not congestion and was not substituted."
            )
            return result
        values.append(
            {
                "id": airport.id,
                "name": airport.name,
                "arrival_delay_share": share.value,
                "arrival_delay_mean_minutes": mean.value,
                "delayed_arrivals": _detail_number(share, "delayed"),
                "operated_arrivals": _detail_number(share, "operated"),
                "source": share.source,
                "retrieved_at": _iso(share.retrieved_at),
                "coverage_note": share.coverage_note,
            }
        )
    result["status"] = "compared"
    result["values"] = values
    result["reason"] = (
        "Congestion is the reporting-carrier share of arrivals delayed at least 15 minutes, "
        "plus the mean arrival-delay minutes, for the same complete month."
    )
    return result


def share(
    session: Session,
    airport: str,
    category: str,
    period: str | None = None,
    conversation_id: int | None = None,
) -> dict:
    result = _base(session, "share", conversation_id)
    result["category"] = category
    result["rule"] = f"Destination great-circle distance of at least {LONG_HAUL_MILES} statute miles."
    result["excluded_destinations"] = None
    result["percentage"] = None
    if category != "long_haul":
        result["status"] = "unsupported_category"
        result["reason"] = "The only flight category this function scores is long-haul."
        return result
    resolved = _resolve_place(session, airport)
    result["airport"] = {
        "query": resolved["query"],
        "status": resolved["status"],
        "name": None if resolved.get("airport") is None else resolved["airport"].name,
        "id": None if resolved.get("airport") is None else resolved["airport"].id,
        "left_out": resolved.get("left_out", []),
    }
    if resolved["status"] != "found":
        result["status"] = "unresolved"
        result["reason"] = "The airport was missing or ambiguous, so no share was calculated."
        return result
    kind = _period_kind(period)
    if kind == "day":
        return _share_day(session, result, resolved["airport"], period)
    return _share_year(session, result, resolved["airport"], period)


def not_routed(session: Session, conversation_id: int | None, reason: str) -> dict:
    result = _base(session, None, conversation_id)
    result["status"] = "not_routed"
    result["ranked"] = []
    result["reason"] = reason
    return result


def run_tool(session: Session, name: str, arguments: dict, conversation_id: int | None = None) -> dict:
    if name == "screen":
        return screen(session, arguments.get("region") or "", arguments.get("project_type"), conversation_id)
    if name == "compare":
        return compare(
            session,
            list(arguments.get("airports") or []),
            arguments.get("metric") or "",
            arguments.get("period"),
            conversation_id,
            periods=arguments.get("periods"),
        )
    if name == "brief":
        return brief(session, conversation_id, int(arguments.get("word_limit") or 150))
    if name == "method":
        return method_note(session, conversation_id)
    if name == "share":
        return share(
            session,
            arguments.get("airport") or "",
            arguments.get("category") or "",
            arguments.get("period"),
            conversation_id,
        )
    if name == "gap":
        return gap(session, arguments.get("airport") or "", arguments.get("quantity") or "", conversation_id)
    return not_routed(session, conversation_id, "The question was not routed to screen, compare, share, or gap.")


def gap(session: Session, airport: str, quantity: str, conversation_id: int | None = None) -> dict:
    result = _base(session, "gap", conversation_id)
    result["quantity"] = quantity
    result["value"] = None
    resolved = _resolve_place(session, airport)
    result["airport"] = {
        "query": airport,
        "status": resolved["status"],
        "name": None if resolved.get("airport") is None else resolved["airport"].name,
    }
    if quantity != "unmet_demand":
        result["status"] = "unknown"
        result["missing"] = [quantity]
        result["reason"] = "That quantity is not defined here. Unmet demand is the only gap this function scores."
        return result
    missing = ["unconstrained_demand", "effective_capacity"]
    result["status"] = "unknown"
    result["missing"] = missing
    result["reason"] = (
        "Unmet demand is unconstrained demand minus effective capacity for the same period and traffic definition. "
        "Served traffic, growth, delay, and a one-day flight count do not fill either input."
    )
    return result


def search_airports_json(session: Session, q: str | None = None, country: str | None = "US", limit: int = 20) -> dict:
    rows = search_airports(session, q, country, limit)
    return {
        "airports": [
            {
                "id": airport.id,
                "name": airport.name,
                "municipality": airport.municipality,
                "iso_country": airport.iso_country,
                "iso_region": airport.iso_region,
                "identifiers": [
                    {"scheme": item.scheme, "value": item.value, "ambiguous": item.ambiguous}
                    for item in airport.identifiers
                ],
            }
            for airport in rows
        ]
    }


def get_observations(session: Session, code: str, metric: str | None = None) -> dict:
    resolved = _resolve_place(session, code)
    if resolved["status"] != "found":
        return {"status": resolved["status"], "observations": [], "matches": resolved.get("matches", [])}
    statement = select(Observation).where(Observation.airport_id == resolved["airport"].id)
    if metric:
        statement = statement.where(Observation.metric == metric)
    rows = session.scalars(statement.order_by(Observation.retrieved_at.desc(), Observation.id.desc())).all()
    return {"status": "found", "observations": [_observation_payload(row) for row in rows]}


def _base(session: Session, function: str, conversation_id: int | None) -> dict:
    evidence = list_evidence(session, conversation_id)
    costs = [item for item in evidence if item["kind"] == "project_cost"]
    schedule = None
    for item in evidence:
        if item["kind"] == "cash_flows":
            schedule = cash_flow_schedule(item["value_text"])
            if schedule:
                break
    if schedule:
        profitability = schedule
    elif costs:
        profitability = {
            "claimed": False,
            "reason": "A project cost is analyst-supplied. It is not a measured API value and it is not a cash-flow forecast.",
        }
    else:
        profitability = {"claimed": False, "reason": "No credible project cash flows are on file."}
    return {
        "function": function,
        "territory": TERRITORY_STATEMENT,
        "evidence": evidence,
        "profitability": profitability,
        "incremental_passengers": "unknown",
        "incremental_movements": "unknown",
        "confidence": "low",
    }


def _screen_reason(status: str) -> str:
    if status == "ranked":
        return "Eligible airport-project pairs are ranked by S. S is not profitability."
    if status == "watchlist":
        return "Demand-growth watchlist. There is no rank because constraint or intervention evidence is missing."
    return "Insufficient data. There is no rank."


def _peers(session: Session, codes: list[str]) -> list[Airport]:
    regions = [f"US-{code}" for code in codes]
    rows = session.scalars(
        select(Airport)
        .options(selectinload(Airport.identifiers))
        .where(
            Airport.iso_country == "US",
            Airport.iso_region.in_(regions),
            Airport.scheduled_service == "yes",
            Airport.airport_type.in_(COMMERCIAL_TYPES),
        )
    ).all()
    return sorted((airport for airport in rows if not is_territory(airport.iso_region)), key=lambda airport: airport.name)


def _enplanement_series(session: Session, airport_ids: list[int]) -> dict[int, dict[int, float]]:
    if not airport_ids:
        return {}
    rows = session.scalars(
        select(Observation)
        .where(
            Observation.airport_id.in_(airport_ids),
            Observation.metric == "enplanements",
            Observation.value.is_not(None),
        )
        .order_by(Observation.retrieved_at.desc(), Observation.id.desc())
    ).all()
    series: dict[int, dict[int, float]] = {}
    for row in rows:
        if row.period_start is None:
            continue
        year_values = series.setdefault(row.airport_id, {})
        year_values.setdefault(row.period_start.year, row.value)
    return series


def _capacity_score(session: Session, airport_id: int, evidence: list[dict]) -> float | None:
    row = session.scalars(
        select(Observation)
        .where(Observation.airport_id == airport_id, Observation.metric == "capacity_pressure")
        .order_by(Observation.retrieved_at.desc(), Observation.id.desc())
    ).first()
    if row is not None and row.value is not None and (row.coverage_note or "").strip() and 0 <= row.value <= 100:
        return row.value
    for item in evidence:
        if item.get("kind") == "capacity_mapping" and item.get("airport_id") == airport_id:
            score = capacity_mapping_score(item.get("value_text") or "")
            if score is not None:
                return score
    return None


def _renovation_fit(evidence: list[dict], airport_id: int) -> tuple[float | None, str]:
    kinds = {item["kind"] for item in evidence if item.get("airport_id") == airport_id}
    if "bottleneck" in kinds and "intervention" in kinds:
        return (
            50,
            "Suggestive analyst-supplied bottleneck and intervention for this airport. R is 50, not a documented 75 or 100.",
        )
    return None, "Renovation fit is unknown for this airport."


def _airport_item(airport: Airport) -> dict:
    return {
        "id": airport.id,
        "name": airport.name,
        "municipality": airport.municipality,
        "iso_region": airport.iso_region,
        "iata": scheme_value(airport, "iata"),
        "faa_lid": scheme_value(airport, "faa_lid"),
    }


def _resolve_place(session: Session, text: str) -> dict:
    query = " ".join((text or "").split())
    key = query.lower().replace(".", "")
    if key in LA_ALIASES:
        return _alias(session, query, "LAX", "Los Angeles")
    if key in SANTA_ANA_ALIASES:
        return _alias(session, query, "SNA", "Santa Ana")
    status, airport, matches = lookup_airport(session, query)
    if status == "ambiguous":
        return {"status": "ambiguous", "query": query, "matches": _match_names(matches)}
    if status == "found":
        return {"status": "found", "query": query, "airport": airport, "left_out": []}
    named = _commercial_in_municipality(session, query)
    if named is not None:
        return named
    found = search_airports(session, query, "US", 5)
    if len(found) == 1:
        return {"status": "found", "query": query, "airport": found[0], "left_out": []}
    if len(found) > 1:
        return {"status": "ambiguous", "query": query, "matches": _match_names(found)}
    return {"status": "missing", "query": query}


def _commercial_in_municipality(session: Session, query: str) -> dict | None:
    name = _place_name(query)
    if not name:
        return None
    rows = [
        airport
        for airport in session.scalars(
            select(Airport)
            .options(selectinload(Airport.identifiers))
            .where(
                func.lower(Airport.municipality) == name,
                Airport.iso_country == "US",
                Airport.scheduled_service == "yes",
                Airport.airport_type.in_(COMMERCIAL_TYPES),
            )
        ).all()
        if not is_territory(airport.iso_region)
    ]
    if not rows:
        return None
    large = [airport for airport in rows if airport.airport_type == "large_airport"]
    if len(large) == 1:
        chosen = large[0]
    elif len(rows) == 1:
        chosen = rows[0]
    else:
        return {"status": "ambiguous", "query": query, "matches": _match_names(rows)}
    left = [{"id": airport.id, "name": airport.name} for airport in rows if airport.id != chosen.id]
    return {"status": "found", "query": query, "airport": chosen, "left_out": left}


def _place_name(query: str) -> str:
    text = " ".join(query.split()).lower()
    for suffix in (" international airport", " airport"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text


def _alias(session: Session, query: str, code: str, municipality: str) -> dict:
    status, airport, matches = lookup_airport(session, code)
    if status != "found":
        return {"status": "missing", "query": query, "defaulted": True}
    left = [
        {"id": other.id, "name": other.name}
        for other in _peers(session, [_region_code(airport)])
        if other.id != airport.id and (other.municipality or "").lower() == municipality.lower()
    ]
    return {"status": "found", "query": query, "airport": airport, "left_out": left, "defaulted": True, "matches": []}


def _region_code(airport: Airport) -> str:
    if airport.iso_region and "-" in airport.iso_region:
        return airport.iso_region.split("-", 1)[1]
    return ""


def _match_names(airports: list[Airport]) -> list[dict]:
    return [{"id": airport.id, "name": airport.name} for airport in airports]


def brief(session: Session, conversation_id: int | None, word_limit: int = 150) -> dict:
    result = _base(session, "brief", conversation_id)
    prior = _previous_scored(session, conversation_id)
    result["word_limit"] = word_limit
    if prior is None:
        summary, count = _files_brief(session, word_limit)
        result["status"] = "summarized"
        result["word_count"] = count
        result["summary"] = summary
        result["reason"] = ""
        return result
    summary, count = _summarize(prior, word_limit)
    result["status"] = "summarized"
    result["prior_function"] = prior.get("function")
    result["word_count"] = count
    result["summary"] = summary
    result["reason"] = "This recap uses only the previous scored answer."
    return result


def method_note(session: Session, conversation_id: int | None) -> dict:
    result = _base(session, "method", conversation_id)
    result["status"] = "explained"
    result["topic"] = "bottleneck"
    result["reason"] = "This is the evidence rule for naming the binding facility. It is not a scored airport."
    result["summary"] = (
        "Identify the specific limiting facility before assigning a capacity score. "
        "A terminal bottleneck is gate or stand utilization against assessed usable capacity, "
        "or passenger-processing throughput against an assessed operating limit, "
        "or published evidence that the terminal is the binding constraint. "
        "A runway bottleneck is peak movements against a credible runway operating capacity. "
        "Runway count, runway length, and annual traffic do not establish it. "
        "An airspace bottleneck is an operating restriction or other airspace constraint. "
        "Physical expansion is not assumed to change regulatory limits or to guarantee new service. "
        "Delays, congestion, peak concentration, and annual throughput against a design figure are supporting evidence only. "
        "They do not name which facility is binding, and delays alone do not establish that renovation is the remedy. "
        "The distinguishing record is a declared bottleneck metric, a credible capacity denominator for that facility, "
        "the period, the source, and a check that another facility or airspace is not the unresolved binding constraint. "
        "Affirmative evidence that the intervention does not address the bottleneck makes the pair ineligible. "
        "Without that mapping, C is not calculated. This is the evidence rule, not a measurement for a named airport."
    )
    return result


def _previous_scored(session: Session, conversation_id: int | None) -> dict | None:
    if conversation_id is None:
        return None
    rows = session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role == "assistant")
        .order_by(Message.id.desc())
    ).all()
    for row in rows:
        if not row.result_json:
            continue
        try:
            payload = json.loads(row.result_json)
        except json.JSONDecodeError:
            continue
        if payload.get("function") in {None, "brief", "method"} or payload.get("status") == "not_routed":
            continue
        return payload
    return None


def _files_brief(session: Session, word_limit: int) -> tuple[str, int]:
    faa = _span(session, "enplanements", monthly=False)
    delay = _span(session, "arrival_delay_share", monthly=True)
    t100 = _span(session, "geocoded_departures", monthly=False)
    text = (
        "Recommendation: do not name a renovation opportunity. This question does not screen a region or supply "
        "a capacity mapping and a relevant intervention, so D, C, and R are not all present and S is not calculated. "
        "S would not be profitability. "
        f"Sources: FAA annual enplanements for {faa}; BTS on-time arrival delay for {delay}; "
        f"BTS T-100 geocoded departures for {t100}. Territory is U.S. states and the District of Columbia. "
        "Assumptions: demand weights 0.40, 0.25, 0.20, and 0.15 are design assumptions, not investment coefficients. "
        "Peers are scheduled commercial-service airports. Six annual observations are required. "
        "Delays, runway count, and runway length are not capacity. A missing figure is not zero. "
        "Biggest uncertainty: capacity pressure, renovation fit, unconstrained demand, and effective capacity "
        "are not established here, so there is no opportunity score and no unmet-demand number. "
        "Confidence is low. Incremental passengers and movements are unknown. Profitability is not claimed."
    )
    words = text.split()
    if len(words) > word_limit:
        text = " ".join(words[:word_limit]).rstrip(".,;") + "."
        words = text.split()
    return text, len(words)


def _span(session: Session, metric: str, monthly: bool) -> str:
    stamps = session.scalars(
        select(Observation.period_start).where(
            Observation.metric == metric,
            Observation.period_start.is_not(None),
            Observation.value.is_not(None),
        )
    ).all()
    if not stamps:
        return "none loaded"
    if monthly:
        labels = sorted({(stamp.year, stamp.month) for stamp in stamps})
        first = f"{labels[0][0]:04d}-{labels[0][1]:02d}"
        last = f"{labels[-1][0]:04d}-{labels[-1][1]:02d}"
        return first if first == last else f"{first} through {last}"
    years = sorted({stamp.year for stamp in stamps})
    return str(years[0]) if len(years) == 1 else f"{years[0]}–{years[-1]}"


def _summarize(prior: dict, word_limit: int) -> tuple[str, int]:
    sections = [
        _recommendation(prior),
        _source_sentence(prior),
        _assumption_sentence(prior),
        _uncertainty_sentence(prior),
    ]
    text = " ".join(section for section in sections if section)
    words = text.split()
    if len(words) > word_limit:
        text = " ".join(words[:word_limit]).rstrip(".,;") + "."
        words = text.split()
    return text, len(words)


def _recommendation(prior: dict) -> str:
    function = prior.get("function")
    if function == "screen":
        ranked = list(prior.get("ranked") or [])
        if ranked:
            top = ranked[0]
            return (
                f"Recommendation: {top.get('name')} leads the opportunity list with S {_one(top.get('opportunity_score'))}. "
                "S is not profitability."
            )
        leaders = [
            item
            for item in prior.get("airports") or []
            if item.get("demand_score") is not None and item.get("opportunity_score") is None
        ]
        leaders.sort(key=lambda item: item["demand_score"], reverse=True)
        if not leaders:
            return "Recommendation: there is no opportunity rank and no demand-growth watchlist."
        named = ", ".join(
            f"{item.get('name')} ({item.get('iata') or item.get('faa_lid') or 'no code'}) {_one(item.get('demand_score'))}"
            for item in leaders[:3]
        )
        project = prior.get("project_type") or "the project"
        return (
            f"Recommendation: do not treat {prior.get('region')} {project} as a ranked renovation opportunity. "
            f"The demand-growth watchlist is led by {named}."
        )
    if function == "compare" and prior.get("sides"):
        bits = []
        for side in prior["sides"]:
            bits.append(
                f"{side.get('name') or side.get('query')} in {side.get('period')} has "
                f"enplanements {side.get('enplanements_label')} and arrival delay {side.get('delay_label')}"
            )
        return "Recommendation: " + "; ".join(bits) + ". These are different periods, so there is no single congestion ranking."
    if function == "compare":
        values = prior.get("values") or []
        if not values:
            return "Recommendation: congestion was not compared because the requested month is not loaded."
        bits = [
            f"{item.get('name')} {_one((item.get('arrival_delay_share') or 0) * 100)} percent delayed, "
            f"mean {_one(item.get('arrival_delay_mean_minutes'))} minutes"
            for item in values
        ]
        return "Recommendation: " + "; ".join(bits) + ". This is a delay comparison, not a renovation rank."
    if function == "share":
        airport = (prior.get("airport") or {}).get("name") or "The airport"
        percentage = prior.get("percentage")
        shown = "unknown" if percentage is None else _one(percentage)
        return f"Recommendation: the long-haul share at {airport} is {shown}."
    if function == "gap":
        airport = (prior.get("airport") or {}).get("name") or "The airport"
        return f"Recommendation: unmet demand at {airport} is unknown. There is no number to use."
    return "Recommendation: the previous answer did not produce a renovation rank."


def _source_sentence(prior: dict) -> str:
    function = prior.get("function")
    if function == "screen":
        window = prior.get("demand_window") or []
        span = f"{window[0]}–{window[-1]}" if window else "the loaded FAA years"
        return f"Sources: FAA annual enplanements for {span}. Territory is U.S. states and the District of Columbia."
    if function == "compare":
        return "Sources: FAA annual enplanements when a calendar year is on file, and BTS on-time arrival delay for each loaded month. A live aircraft count is not a source."
    if function == "share":
        return "Sources: BTS T-100 geocoded departures for a year, or one OpenSky departure day when that sample was used."
    if function == "gap":
        missing = ", ".join(prior.get("missing") or [])
        return f"Sources: none on file for {missing or 'the missing inputs'}."
    return "Sources: the previous scored record."


def _assumption_sentence(prior: dict) -> str:
    function = prior.get("function")
    if function == "screen":
        return (
            "Assumptions: demand weights 0.40, 0.25, 0.20, and 0.15 are design assumptions, not investment coefficients. "
            "Peers are scheduled commercial-service airports. Six annual observations are required. Delays are not capacity."
        )
    if function == "compare":
        return "Assumptions: enplanements are boardings, not total terminal passengers. Arrival delay is a reporting-carrier month, not a year, and months are not averaged."
    if function == "share":
        return "Assumptions: long-haul means a destination at least 3000 statute miles away. Unknown destinations are excluded, not counted as short-haul."
    if function == "gap":
        return "Assumptions: unmet demand is unconstrained demand minus effective capacity for the same period. Served traffic, growth, and delay do not fill either input."
    return "Assumptions: missing evidence stays missing."


def _uncertainty_sentence(prior: dict) -> str:
    profit = prior.get("profitability") or {}
    if profit.get("claimed") and profit.get("npv") is not None:
        profit_line = f" Profitability is claimed at NPV {profit.get('npv')} {profit.get('currency')}."
    else:
        profit_line = " Profitability is not claimed."
    if prior.get("function") == "gap":
        return "Biggest uncertainty: both unmet-demand inputs are missing, so the gap has no value. Confidence is low." + profit_line
    if prior.get("function") == "compare" and prior.get("sides"):
        return "Biggest uncertainty: the years are not one complete month, and a missing year is not zero. Confidence is low." + profit_line
    if prior.get("status") == "ranked":
        return "Biggest uncertainty: incremental passengers and movements are unknown, and robustness tests were not run. Confidence is low." + profit_line
    return (
        "Biggest uncertainty: capacity pressure or renovation fit is missing, so there is no opportunity score. "
        "Confidence is low. Incremental passengers and movements are unknown."
        + profit_line
    )


def _one(value) -> str:
    if value is None:
        return "unknown"
    number = float(value)
    rounded = math.floor(number * 10 + 0.5) / 10
    return f"{rounded:.1f}"


def _compare_separate_periods(session: Session, result: dict, airports: list[str], periods: list[str]) -> dict:
    result["metric"] = "enplanements_and_arrival_delay"
    result["substituted_live_traffic"] = False
    if len(periods) != len(airports) or not airports:
        result["status"] = "unresolved"
        result["reason"] = "Each airport in this comparison needs its own period."
        return result
    resolved = [_resolve_place(session, name) for name in airports]
    result["airports"] = [
        {
            "query": item["query"],
            "status": item["status"],
            "name": None if item.get("airport") is None else item["airport"].name,
            "id": None if item.get("airport") is None else item["airport"].id,
            "left_out": item.get("left_out", []),
            "matches": item.get("matches", []),
            "defaulted": item.get("defaulted", False),
        }
        for item in resolved
    ]
    if any(item["status"] != "found" for item in resolved):
        result["status"] = "unresolved"
        result["reason"] = "One or more airports were missing or ambiguous, so no comparison was calculated."
        return result
    sides = []
    for item, phrase in zip(resolved, periods, strict=True):
        canonical = _canonical_period(phrase)
        enplanements, delays = _history_for_period(session, item["airport"].id, canonical)
        sides.append(
            {
                "query": item["query"],
                "name": item["airport"].name,
                "id": item["airport"].id,
                "period": canonical or phrase,
                "enplanements": None if enplanements is None else enplanements.value,
                "enplanements_label": _passengers(None if enplanements is None else enplanements.value),
                "delay_label": _delay_label(delays),
                "retrieved_label": _retrieved_label(enplanements, delays),
                "left_out": item.get("left_out", []),
            }
        )
    result["sides"] = sides
    loaded = [side for side in sides if side["enplanements_label"] != "not loaded" or side["delay_label"] != "not loaded"]
    result["status"] = "compared" if len(loaded) == len(sides) else "partial" if loaded else "unavailable"
    left = [
        f"{side['query']} left out {', '.join(item['name'] for item in side['left_out'])}"
        for side in sides
        if side.get("left_out")
    ]
    result["reason"] = (
        "Each airport keeps the year or month that was asked. FAA enplanements are annual boardings. "
        "BTS arrival delay is reported only for loaded months and is not averaged into a year. "
        "A missing figure is not zero. Different periods are not one congestion ranking."
    )
    if left:
        result["reason"] += " " + ". ".join(left) + "."
    return result


def _canonical_period(phrase: str) -> str | None:
    text = " ".join((phrase or "").lower().split())
    now = clock.utcnow()
    if text in {"this year", "current year"}:
        return f"{now.year:04d}"
    if text in {"last year", "previous year"}:
        return f"{now.year - 1:04d}"
    if text == "this month":
        return f"{now.year:04d}-{now.month:02d}"
    if text in {"last month", "previous month"}:
        year = now.year if now.month > 1 else now.year - 1
        month = now.month - 1 or 12
        return f"{year:04d}-{month:02d}"
    if re.fullmatch(r"20\d{2}", text):
        return text
    if re.fullmatch(r"20\d{2}-\d{2}", text):
        month = int(text[5:7])
        if 1 <= month <= 12:
            return text
    return None


def _history_for_period(session: Session, airport_id: int, period: str | None):
    if period and len(period) == 4 and period.isdigit():
        year = int(period)
        return _year_enplanements(session, airport_id, year), _delay_months(session, airport_id, year, None)
    if period and len(period) == 7:
        start, end, _label = _month_period(period)
        return _year_enplanements(session, airport_id, start.year), _delay_months(session, airport_id, start.year, start.month)
    return None, []


def _year_enplanements(session: Session, airport_id: int, year: int) -> Observation | None:
    return _metric_row(session, airport_id, "enplanements", datetime(year, 1, 1), datetime(year + 1, 1, 1))


def _delay_months(session: Session, airport_id: int, year: int, month: int | None) -> list[dict]:
    start = datetime(year, month or 1, 1)
    end = datetime(year + 1, 1, 1) if month is None else (datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1))
    shares = session.scalars(
        select(Observation)
        .where(
            Observation.airport_id == airport_id,
            Observation.metric == "arrival_delay_share",
            Observation.period_start >= start,
            Observation.period_start < end,
            Observation.value.is_not(None),
        )
        .order_by(Observation.period_start, Observation.retrieved_at.desc())
    ).all()
    seen = set()
    months = []
    for share in shares:
        if share.period_start in seen:
            continue
        seen.add(share.period_start)
        mean = _metric_row(session, airport_id, "arrival_delay_mean_minutes", share.period_start, share.period_end)
        if mean is None or mean.value is None:
            continue
        months.append(
            {
                "label": f"{share.period_start.year:04d}-{share.period_start.month:02d}",
                "share": share.value,
                "mean": mean.value,
                "retrieved_at": share.retrieved_at,
            }
        )
    return months


def _passengers(value) -> str:
    if value is None:
        return "not loaded"
    number = float(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.1f}"


def _delay_label(months: list[dict]) -> str:
    if not months:
        return "not loaded"
    return "; ".join(
        f"{item['label']}: {item['share'] * 100:.1f}% delayed, mean {item['mean']:.1f} min" for item in months
    )


def _retrieved_label(enplanements: Observation | None, months: list[dict]) -> str:
    stamps = []
    if enplanements is not None:
        stamps.append(enplanements.retrieved_at)
    stamps.extend(item["retrieved_at"] for item in months)
    if not stamps:
        return "not loaded"
    latest = max(stamps)
    return latest.date().isoformat()


def _latest_ontime_label(session: Session) -> str | None:
    latest = session.scalar(
        select(func.max(Observation.period_start)).where(
            Observation.source == "bts_ontime",
            Observation.metric == "arrival_delay_share",
            Observation.value.is_not(None),
        )
    )
    if latest is None:
        return None
    return f"{latest.year:04d}-{latest.month:02d}"


def _month_period(period: str | None) -> tuple[datetime, datetime, str]:
    if period and len(period) == 7 and period[4] == "-":
        year = int(period[:4])
        month = int(period[5:7])
        start = datetime(year, month, 1)
        end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
        return start, end, f"{year:04d}-{month:02d}"
    today = clock.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = today
    start = (end - timedelta(days=1)).replace(day=1)
    return start, end, f"{start.year:04d}-{start.month:02d}"


def _period_kind(period: str | None) -> str:
    if period and len(period) == 10 and period[4] == "-" and period[7] == "-":
        return "day"
    return "year"


def _metric_row(session: Session, airport_id: int, metric: str, start: datetime, end: datetime) -> Observation | None:
    return session.scalars(
        select(Observation)
        .where(
            Observation.airport_id == airport_id,
            Observation.metric == metric,
            Observation.period_start == start,
            Observation.period_end == end,
        )
        .order_by(Observation.retrieved_at.desc(), Observation.id.desc())
    ).first()


def _detail_number(observation: Observation, key: str):
    if not observation.detail:
        return None
    try:
        return json.loads(observation.detail).get(key)
    except json.JSONDecodeError:
        return None


def _share_year(session: Session, result: dict, airport: Airport, period: str | None) -> dict:
    year = int(period) if period and period.isdigit() and len(period) == 4 else None
    rows = session.scalars(
        select(Observation).where(
            Observation.airport_id == airport.id,
            Observation.source == "bts_t100",
            Observation.metric.in_(("long_haul_departures", "geocoded_departures", "unknown_destination_departures")),
        )
    ).all()
    if year is None:
        years = sorted({row.period_start.year for row in rows if row.period_start is not None})
        if not years:
            result["status"] = "unavailable"
            result["period"] = None
            result["source"] = "bts_t100"
            result["reason"] = "A full-year long-haul share needs BTS T-100 departures. None are loaded for this airport."
            return result
        year = years[-1]
    chosen = [row for row in rows if row.period_start is not None and row.period_start.year == year]
    months = sorted({row.period_start.month for row in chosen})
    totals = {"long_haul_departures": 0.0, "geocoded_departures": 0.0, "unknown_destination_departures": 0.0}
    excluded_destinations = set()
    for row in chosen:
        if row.value is not None and row.metric in totals:
            totals[row.metric] += row.value
        if row.metric == "unknown_destination_departures" and row.detail:
            try:
                excluded_destinations.update(json.loads(row.detail).get("destinations", []))
            except json.JSONDecodeError:
                pass
    geocoded = totals["geocoded_departures"]
    long_haul = totals["long_haul_departures"]
    complete = len(months) == 12
    result.update(
        status="computed" if geocoded and complete else "incomplete_year" if chosen else "unavailable",
        period={"label": str(year), "months": months, "complete_year": complete},
        source="bts_t100",
        sample="full year of BTS T-100 departures" if complete else "incomplete T-100 year",
        long_haul_departures=long_haul,
        geocoded_departures=geocoded,
        excluded_departures=totals["unknown_destination_departures"],
        excluded_destinations=len(excluded_destinations),
        percentage=(100.0 * long_haul / geocoded) if geocoded else None,
        coverage_note=(
            "BTS T-100 segment departures performed. Distance is the published great-circle miles. "
            "Unknown distances are excluded, not counted as short-haul. Not annual passengers."
        ),
    )
    if result["status"] == "unavailable":
        result["reason"] = f"No BTS T-100 departures are loaded for {year}."
    elif not geocoded:
        result["status"] = "unavailable"
        result["reason"] = "No departures with a published distance are loaded, so the percentage is unknown."
    elif not complete:
        result["reason"] = "The T-100 year is incomplete, so this is not an annual share."
    else:
        result["reason"] = "Share of geocoded departures whose destination is at least 3000 statute miles away."
    return result


def _share_day(session: Session, result: dict, airport: Airport, period: str) -> dict:
    day = datetime.fromisoformat(period)
    end = day + timedelta(days=1)
    observation = _metric_row(session, airport.id, "departure_flights", day, end)
    result["period"] = {"label": period, "complete_year": False}
    result["source"] = "opensky"
    result["sample"] = "one-day tracked sample"
    if observation is None or observation.snapshot_id is None:
        result["status"] = "unavailable"
        result["reason"] = "No OpenSky departure list is stored for that day. A missing list is not zero long-haul flights."
        return result
    snapshot = session.get(RawSnapshot, observation.snapshot_id)
    try:
        flights = json.loads(snapshot.body) if snapshot is not None else None
    except json.JSONDecodeError:
        flights = None
    if not isinstance(flights, list):
        result["status"] = "unavailable"
        result["reason"] = "The stored departure snapshot is not a flight list."
        return result
    long_haul = 0
    geocoded = 0
    excluded = set()
    for flight in flights:
        code = None if not isinstance(flight, dict) else flight.get("estArrivalAirport")
        if not code:
            excluded.add("(blank)")
            continue
        status, dest, _matches = lookup_airport(session, str(code))
        if status != "found" or dest.latitude is None or dest.longitude is None or airport.latitude is None:
            excluded.add(str(code))
            continue
        distance = _statute_miles(airport.latitude, airport.longitude, dest.latitude, dest.longitude)
        geocoded += 1
        if distance >= LONG_HAUL_MILES:
            long_haul += 1
    result.update(
        status="computed" if geocoded else "unavailable",
        long_haul_departures=long_haul,
        geocoded_departures=geocoded,
        excluded_departures=len(flights) - geocoded,
        excluded_destinations=len(excluded),
        percentage=(100.0 * long_haul / geocoded) if geocoded else None,
        coverage_note="OpenSky one-day tracked sample. Not an annual share. Unknown destinations are excluded.",
        reason=(
            "Share of geocoded departures whose destination is at least 3000 statute miles away."
            if geocoded
            else "No departure in this sample has a geocoded destination, so the percentage is unknown."
        ),
    )
    return result


def _statute_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 3958.7613
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _observation_payload(row: Observation) -> dict:
    return {
        "id": row.id,
        "metric": row.metric,
        "value": row.value,
        "unit": row.unit,
        "period_start": _iso(row.period_start),
        "period_end": _iso(row.period_end),
        "source": row.source,
        "retrieved_at": _iso(row.retrieved_at),
        "value_kind": row.value_kind,
        "coverage_note": row.coverage_note,
    }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat() + "Z"
