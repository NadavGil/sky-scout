import asyncio
import json
import zipfile
from datetime import datetime
from io import BytesIO

import httpx

from app import clock
from app.chat.gemini import GeminiModel, candidate_models, explain_body, endpoint, parse_explanation, parse_route, route_body
from app.config import Settings
from app.db import prepare_database, session_scope
from app.mcp.__main__ import handle
from app.mcp.tools import call_tool
from app.models import Conversation, Observation, RawSnapshot
from app.scoring.evidence import record_analyst_evidence
from app.scoring.formulas import opportunity_score
from app.scoring.service import compare, gap, screen, share
from app.services.catalog import import_catalog
from app.services.stats import import_faa_csv, import_faa_xlsx, import_ontime_csv, import_t100_csv
from tests.samples import airports_csv


def test_answers_name_demand_order_and_delay_percentages():
    from app.scoring.explain import explain_locally

    screen_text = explain_locally(
        {
            "function": "screen",
            "territory": "U.S. states and the District of Columbia. Territories are excluded.",
            "region": "New England",
            "state_names": ["Connecticut"],
            "peer_set": "Scheduled commercial-service airports.",
            "project_type": "terminal expansion",
            "reason": "Demand-growth watchlist. There is no rank because constraint or intervention evidence is missing.",
            "ranked": [],
            "airports": [
                {"name": "Boston Logan", "iata": "BOS", "demand_score": 67.0},
                {"name": "Tweed New Haven", "iata": "HVN", "demand_score": 90.75},
            ],
        }
    )
    assert screen_text.index("Tweed New Haven") < screen_text.index("Boston Logan")
    assert "demand score 90.8." in screen_text
    assert "not an opportunity rank" in screen_text
    compared = explain_locally(
        {
            "function": "compare",
            "territory": "U.S. states and the District of Columbia. Territories are excluded.",
            "period": {"label": "2026-08"},
            "reason": "Congestion is the reporting-carrier share.",
            "airports": [],
            "values": [{"name": "Los Angeles International Airport", "arrival_delay_share": 0.247, "arrival_delay_mean_minutes": 10.4}],
        }
    )
    assert "24.7 percent of arrivals" in compared
    assert "mean 10.4 minutes" in compared


def test_t100_form_requests_segment_fields_for_one_year():
    from app.services.stats import t100_form_fields

    html = (
        '<input id="__VIEWSTATE" value="abc" />'
        '<input id="__VIEWSTATEGENERATOR" value="gen" />'
        '<input id="__EVENTVALIDATION" value="ev" />'
    )
    fields = t100_form_fields(html, 2025)
    assert fields["cboYear"] == "2025"
    assert fields["cboPeriod"] == "All"
    assert fields["DEPARTURES_PERFORMED"] == "on"
    assert fields["DISTANCE"] == "on"
    assert fields["__VIEWSTATE"] == "abc"


def test_faa_workbook_urls_match_the_published_files():
    settings = Settings()
    assert settings.faa_years[0] == 2020
    assert settings.faa_enplanements_url(2021).endswith("/2022-09/cy21-commercial-service-enplanements.xlsx")
    assert settings.faa_enplanements_url(2025).endswith("arp-cy2025-commercial-service-enplanements.xlsx")


def _factory():
    return prepare_database(Settings(database_url="sqlite://", gemini_api_key=""))


def _load(session):
    import_catalog(
        session,
        airports_csv(
            [
                {
                    "id": "1",
                    "ident": "KBOS",
                    "type": "large_airport",
                    "name": "Boston Logan",
                    "latitude_deg": "42.36",
                    "longitude_deg": "-71.01",
                    "iso_country": "US",
                    "iso_region": "US-MA",
                    "municipality": "Boston",
                    "scheduled_service": "yes",
                    "gps_code": "KBOS",
                    "iata_code": "BOS",
                    "local_code": "BOS",
                },
                {
                    "id": "2",
                    "ident": "KPWM",
                    "type": "large_airport",
                    "name": "Portland Jetport",
                    "latitude_deg": "43.65",
                    "longitude_deg": "-70.31",
                    "iso_country": "US",
                    "iso_region": "US-ME",
                    "municipality": "Portland",
                    "scheduled_service": "yes",
                    "gps_code": "KPWM",
                    "iata_code": "PWM",
                    "local_code": "PWM",
                },
                {
                    "id": "3",
                    "ident": "KLAX",
                    "type": "large_airport",
                    "name": "Los Angeles International",
                    "latitude_deg": "33.94",
                    "longitude_deg": "-118.41",
                    "iso_country": "US",
                    "iso_region": "US-CA",
                    "municipality": "Los Angeles",
                    "scheduled_service": "yes",
                    "gps_code": "KLAX",
                    "iata_code": "LAX",
                    "local_code": "LAX",
                },
                {
                    "id": "4",
                    "ident": "TJSJ",
                    "type": "large_airport",
                    "name": "San Juan",
                    "latitude_deg": "18.44",
                    "longitude_deg": "-66.0",
                    "iso_country": "US",
                    "iso_region": "US-PR",
                    "municipality": "San Juan",
                    "scheduled_service": "yes",
                    "gps_code": "TJSJ",
                    "iata_code": "SJU",
                    "local_code": "SJU",
                },
                {
                    "id": "5",
                    "ident": "PANC",
                    "type": "large_airport",
                    "name": "Ted Stevens Anchorage",
                    "latitude_deg": "61.174",
                    "longitude_deg": "-149.996",
                    "iso_country": "US",
                    "iso_region": "US-AK",
                    "municipality": "Anchorage",
                    "scheduled_service": "yes",
                    "gps_code": "PANC",
                    "iata_code": "ANC",
                    "local_code": "ANC",
                },
                {
                    "id": "6",
                    "ident": "KSEA",
                    "type": "large_airport",
                    "name": "Seattle Tacoma",
                    "latitude_deg": "47.45",
                    "longitude_deg": "-122.31",
                    "iso_country": "US",
                    "iso_region": "US-WA",
                    "municipality": "Seattle",
                    "scheduled_service": "yes",
                    "gps_code": "KSEA",
                    "iata_code": "SEA",
                    "local_code": "SEA",
                },
                {
                    "id": "7",
                    "ident": "RJAA",
                    "type": "large_airport",
                    "name": "Narita",
                    "latitude_deg": "35.76",
                    "longitude_deg": "140.39",
                    "iso_country": "JP",
                    "iso_region": "JP-12",
                    "municipality": "Tokyo",
                    "scheduled_service": "yes",
                    "gps_code": "RJAA",
                    "iata_code": "NRT",
                },
            ]
        ),
        "https://davidmegginson.github.io/ourairports-data/airports.csv",
    )


def test_a_city_name_uses_the_large_commercial_airport():
    factory = _factory()
    with session_scope(factory) as session:
        import_catalog(
            session,
            airports_csv(
                [
                    {
                        "id": "1",
                        "ident": "PANC",
                        "type": "large_airport",
                        "name": "Ted Stevens Anchorage International Airport",
                        "latitude_deg": "61.17",
                        "longitude_deg": "-150.00",
                        "iso_country": "US",
                        "iso_region": "US-AK",
                        "municipality": "Anchorage",
                        "scheduled_service": "yes",
                        "gps_code": "PANC",
                        "iata_code": "ANC",
                        "local_code": "ANC",
                    },
                    {
                        "id": "2",
                        "ident": "PAMR",
                        "type": "medium_airport",
                        "name": "Merrill Field",
                        "latitude_deg": "61.21",
                        "longitude_deg": "-149.84",
                        "iso_country": "US",
                        "iso_region": "US-AK",
                        "municipality": "Anchorage",
                        "scheduled_service": "yes",
                        "gps_code": "PAMR",
                        "iata_code": "MRI",
                        "local_code": "MRI",
                    },
                    {
                        "id": "3",
                        "ident": "PALH",
                        "type": "seaplane_base",
                        "name": "Lake Hood Seaplane Base",
                        "latitude_deg": "61.18",
                        "longitude_deg": "-149.96",
                        "iso_country": "US",
                        "iso_region": "US-AK",
                        "municipality": "Anchorage",
                        "scheduled_service": "no",
                        "gps_code": "PALH",
                    },
                ]
            ),
            "https://davidmegginson.github.io/ourairports-data/airports.csv",
        )
        shared = share(session, "Anchorage airport", "long_haul")
        assert shared["airport"]["name"] == "Ted Stevens Anchorage International Airport"
        assert [item["name"] for item in shared["airport"]["left_out"]] == ["Merrill Field"]
        assert shared["status"] == "unavailable"


def test_growth_alone_does_not_rank_and_zero_score_stays_zero():
    assert opportunity_score(0, 80, 80) == 0
    assert opportunity_score(None, 80, 80) is None
    factory = _factory()
    with session_scope(factory) as session:
        _load(session)
        lines = ["faa_lid,year,enplanements"]
        for year, boston, portland in zip(
            range(2019, 2025),
            (100, 110, 120, 130, 140, 150),
            (100, 100, 100, 100, 100, 200),
            strict=True,
        ):
            lines.append(f"BOS,{year},{boston}")
            lines.append(f"PWM,{year},{portland}")
            lines.append(f"ZZZ,{year},1")
            lines.append(f"SJU,{year},9")
        import_faa_csv(session, "\n".join(lines) + "\n", "https://www.faa.gov/example/enplanements.csv")
        result = screen(session, "New England", "terminal expansion")
        assert result["status"] == "watchlist"
        assert result["ranked"] == []
        assert {item["name"] for item in result["airports"]} == {"Boston Logan", "Portland Jetport"}
        assert all(item["classification"] == "demand_growth_watchlist" for item in result["airports"])
        assert all("opportunity_score" not in item for item in result["airports"])
        stored = session.query(Observation).all()
        assert all(row.value != 1 for row in stored)
        assert all(row.value != 9 for row in stored)


def test_opportunity_score_uses_only_complete_evidence():
    factory = _factory()
    with session_scope(factory) as session:
        _load(session)
        lines = ["faa_lid,year,enplanements"]
        for year, boston, portland in zip(range(2019, 2025), (100, 110, 120, 130, 140, 150), (100, 100, 100, 100, 100, 200), strict=True):
            lines.append(f"BOS,{year},{boston}")
            lines.append(f"PWM,{year},{portland}")
        import_faa_csv(session, "\n".join(lines) + "\n", "https://www.faa.gov/example/enplanements.csv")
        from app.services.catalog import lookup_airport

        _status, airport, _matches = lookup_airport(session, "BOS")
        conversation = Conversation(created_at=clock.utcnow())
        session.add(conversation)
        session.flush()
        record_analyst_evidence(session, conversation.id, "bottleneck", "gate shortage", airport.id)
        record_analyst_evidence(session, conversation.id, "intervention", "add gates", airport.id)
        session.add(
            Observation(
                airport_id=airport.id,
                metric="capacity_pressure",
                value=80,
                unit="score",
                source="analyst",
                retrieved_at=clock.utcnow(),
                value_kind="analyst_assumed",
                quality_flags="[]",
                coverage_note="Declared mapping: 80 means the published gate limit is 80 percent used.",
            )
        )
        session.commit()
        result = screen(session, "New England", "terminal expansion", conversation.id)
        ranked = result["ranked"]
        assert len(ranked) == 1
        assert ranked[0]["id"] == airport.id
        assert ranked[0]["opportunity_score"] == opportunity_score(
            ranked[0]["demand_score"], ranked[0]["capacity_score"], ranked[0]["renovation_fit"]
        )
        assert result["profitability"]["claimed"] is False


def test_congestion_and_long_haul_numbers():
    factory = _factory()
    with session_scope(factory) as session:
        _load(session)
        ontime = "\n".join(
            [
                "Year,Month,Dest,ArrDel15,ArrDelay,Cancelled",
                "2024,6,LAX,1,20,0",
                "2024,6,LAX,0,5,0",
                "2024,6,LAX,1,,0",
                "2024,6,LAX,1,30,1",
            ]
        )
        import_ontime_csv(session, ontime, "https://transtats.bts.gov/example/ontime.csv")
        result = compare(session, ["LAX"], "congestion", "2024-06")
        assert result["substituted_live_traffic"] is False
        assert result["values"][0]["arrival_delay_share"] == 2 / 3
        assert result["values"][0]["arrival_delay_mean_minutes"] == 12.5
        assert compare(session, ["LAX"], "congestion")["period"]["label"] == "2024-06"
        assert call_tool(session, "compare", {"airports": ["LAX"], "metric": "congestion", "period": "2024-06"}) == result

        import_t100_csv(
            session,
            "ORIGIN,DEST,YEAR,MONTH,DEPARTURES_PERFORMED,DISTANCE\nANC,NRT,2024,6,10,3460\nANC,SEA,2024,6,30,1448\nANC,ZZZ,2024,6,4,\n",
            "https://transtats.bts.gov/example/t100.csv",
        )
        shared = share(session, "Anchorage", "long_haul", "2024")
        assert shared["percentage"] == 25.0
        assert shared["excluded_destinations"] == 1
        assert shared["period"]["complete_year"] is False
        assert call_tool(session, "share", {"airport": "Anchorage", "category": "long_haul", "period": "2024"}) == shared

        day = datetime(2024, 11, 2)
        snapshot = RawSnapshot(
            airport_id=shared["airport"]["id"],
            source="opensky",
            kind="departure",
            request_params="{}",
            body=json.dumps(
                [
                    {"estArrivalAirport": "RJAA"},
                    {"estArrivalAirport": "KSEA"},
                    {"estArrivalAirport": None},
                ]
            ),
            retrieved_at=clock.utcnow(),
        )
        session.add(snapshot)
        session.flush()
        session.add(
            Observation(
                airport_id=shared["airport"]["id"],
                snapshot_id=snapshot.id,
                metric="departure_flights",
                value=3,
                unit="flights",
                period_start=day,
                period_end=datetime(2024, 11, 3),
                source="opensky",
                retrieved_at=clock.utcnow(),
                value_kind="measured",
                quality_flags="[]",
                coverage_note="sample",
            )
        )
        session.commit()
        one_day = share(session, "ANC", "long_haul", "2024-11-02")
        assert one_day["sample"] == "one-day tracked sample"
        assert one_day["percentage"] == 50.0
        assert one_day["excluded_destinations"] == 1
        assert gap(session, "SFO", "unmet_demand")["status"] == "unknown"


def _workbook() -> bytes:
    sheet = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="inlineStr"><is><t>CY enplanements</t></is></c></row>
    <row r="2">
      <c r="A2" t="inlineStr"><is><t>Locid</t></is></c>
      <c r="B2" t="inlineStr"><is><t>ST</t></is></c>
      <c r="C2" t="inlineStr"><is><t>CY 24 Enplanements</t></is></c>
    </row>
    <row r="3">
      <c r="A3" t="inlineStr"><is><t>BOS</t></is></c>
      <c r="B3" t="inlineStr"><is><t>MA</t></is></c>
      <c r="C3"><v>1234</v></c>
    </row>
    <row r="4">
      <c r="A4" t="inlineStr"><is><t>SJU</t></is></c>
      <c r="B4" t="inlineStr"><is><t>PR</t></is></c>
      <c r="C4"><v>50</v></c>
    </row>
  </sheetData>
</worksheet>
"""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


def test_faa_workbook_skips_territories():
    factory = _factory()
    with session_scope(factory) as session:
        _load(session)
        imported = import_faa_xlsx(session, _workbook(), "https://www.faa.gov/example/enplanements.xlsx")
        assert imported["row_count"] == 1
        assert imported["skipped_territory"] == 1
        values = [row.value for row in session.query(Observation).all()]
        assert 1234 in values
        assert 50 not in values


def test_mcp_tools_match_functions_and_evidence_is_not_an_observation():
    factory = _factory()
    with session_scope(factory) as session:
        _load(session)
        assert call_tool(session, "gap", {"airport": "SFO", "quantity": "unmet_demand"}) == gap(session, "SFO", "unmet_demand")
        assert call_tool(session, "screen", {"region": "New England", "project_type": "terminal expansion"}) == screen(
            session, "New England", "terminal expansion"
        )
        conversation = Conversation(created_at=clock.utcnow())
        session.add(conversation)
        session.flush()
        direct = record_analyst_evidence(session, conversation.id, "project_cost", "10 USD")
        via = call_tool(
            session,
            "record_analyst_evidence",
            {"conversation_id": conversation.id, "kind": "project_cost", "value_text": "10 USD"},
        )
        assert {key: direct[key] for key in ("kind", "value_text", "value_kind")} == {
            key: via[key] for key in ("kind", "value_text", "value_kind")
        }
        assert session.query(Observation).count() == 0
        listed = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, factory)
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert names == {
            "screen",
            "compare",
            "share",
            "gap",
            "search_airports",
            "get_observations",
            "record_analyst_evidence",
            "brief",
            "method",
        }


def test_gemini_request_routes_without_web_search_and_explains_from_json():
    body = route_body([{"role": "assistant", "content": "Earlier turn."}, {"role": "user", "content": "New England terminals"}])
    assert endpoint("gemini-3.8-flash").endswith("/models/gemini-3.8-flash:generateContent")
    assert body["generationConfig"]["temperature"] == 0
    assert body["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO"
    assert body["contents"][0]["role"] == "model"
    names = {item["name"] for item in body["tools"][0]["functionDeclarations"]}
    assert names == {"screen", "compare", "share", "gap", "brief", "method"}
    assert "googleSearch" not in json.dumps(body)
    routed = parse_route(
        {
            "candidates": [
                {"content": {"parts": [{"functionCall": {"name": "screen", "args": {"region": "New England"}}}]}}
            ]
        }
    )
    assert routed == {"name": "screen", "arguments": {"region": "New England"}}
    explanation = explain_body("skill", {"status": "insufficient_data"})
    assert "tools" not in explanation
    assert parse_explanation(
        {"candidates": [{"content": {"parts": [{"thought": True, "text": "hidden"}, {"text": "No rank."}]}}]}
    ) == "No rank."
    assert candidate_models("gemini-3.8-flash") == [
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
    ]


def test_gemini_uses_a_lighter_model_when_the_configured_one_is_unavailable():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/gemini-3.8-flash:generateContent"):
            return httpx.Response(503, json={"error": {"code": 503, "message": "high demand"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"functionCall": {"name": "screen", "args": {"region": "New England"}}}]}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            model = GeminiModel(client, Settings(gemini_api_key="test-key", gemini_model="gemini-3.8-flash"), _sleep)
            route = await model.route([{"role": "user", "content": "New England terminals"}])
            assert model.active_model == "gemini-3.5-flash-lite"
            return route

    assert asyncio.run(run()) == {"name": "screen", "arguments": {"region": "New England"}}
    assert calls[0].endswith("/gemini-3.8-flash:generateContent")
    assert calls[1].endswith("/gemini-3.5-flash-lite:generateContent")


def test_gemini_tries_the_next_model_when_the_configured_one_times_out():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gemini-3.8-flash:generateContent"):
            raise httpx.ReadTimeout("timed out")
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "No rank."}]}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            model = GeminiModel(client, Settings(gemini_api_key="test-key", gemini_model="gemini-3.8-flash"), _sleep)
            return await model.explain("skill", {"status": "insufficient_data"})

    assert asyncio.run(run()) == "No rank."


def test_analyst_mapping_and_cash_flows_can_finish_a_rank():
    from app.scoring.explain import explain_locally
    from app.scoring.finance import cash_flow_schedule
    from app.services.catalog import lookup_airport

    assert cash_flow_schedule("discount_rate: 0.1\ncurrency: USD") is None
    factory = _factory()
    with session_scope(factory) as session:
        _load(session)
        lines = ["faa_lid,year,enplanements"]
        for year, boston, portland in zip(
            range(2019, 2025),
            (100, 110, 120, 130, 140, 150),
            (100, 100, 100, 100, 100, 200),
            strict=True,
        ):
            lines.append(f"BOS,{year},{boston}")
            lines.append(f"PWM,{year},{portland}")
        import_faa_csv(session, "\n".join(lines) + "\n", "https://www.faa.gov/example/enplanements.csv")
        _status, airport, _matches = lookup_airport(session, "BOS")
        conversation = Conversation(created_at=clock.utcnow())
        session.add(conversation)
        session.flush()
        record_analyst_evidence(session, conversation.id, "bottleneck", "gate shortage", airport.id)
        record_analyst_evidence(session, conversation.id, "intervention", "add gates", airport.id)
        record_analyst_evidence(
            session,
            conversation.id,
            "capacity_mapping",
            "80 | Declared mapping: the published gate limit is 80 percent used.",
            airport.id,
        )
        record_analyst_evidence(
            session,
            conversation.id,
            "cash_flows",
            "\n".join(
                [
                    "discount_rate: 0.1",
                    "currency: USD",
                    "inflation: nominal",
                    "perspective: airport sponsor",
                    "2027,10,2,4,1,20,1",
                    "2028,12,3,4,1,0,0",
                ]
            ),
        )
        session.commit()
        result = screen(session, "New England", "terminal expansion", conversation.id)
        assert result["ranked"][0]["capacity_score"] == 80
        assert result["profitability"]["npv"] == -4.91
        assert result["profitability"]["claimed"] is True
        assert session.query(Observation).filter(Observation.metric == "capacity_pressure").count() == 0
        text = explain_locally(result)
        assert "D " in text and "S " in text
        assert "NPV -4.91 USD" in text
        assert "Incremental passengers and movements are unknown." in text
        from app.chat.agent import _single_airport_id
        from app.scoring.evidence import extract_evidence

        message = "Screen BOS\ncapacity mapping: 80 | gate note\n\nWhat about PWM and BOS?"
        assert _single_airport_id(session, "Screen BOS only") == airport.id
        assert _single_airport_id(session, message) is None
        found = extract_evidence("Question\n\nCash flows:\ndiscount_rate: 0.1\ncurrency: USD\n")
        assert found[0][0] == "cash_flows"
        assert "discount_rate: 0.1" in found[0][1]


def test_follow_up_questions_stay_on_the_asked_subject():
    from app.chat.local import follow_up
    from app.models import Message
    from app.scoring.explain import explain_locally

    brief_route = follow_up("Summarize your recommendation, sources, assumptions, and biggest uncertainty in 150 words.")
    assert brief_route["name"] == "brief"
    assert brief_route["arguments"]["word_limit"] == 150
    method_route = follow_up("What evidence would distinguish a terminal bottleneck from a runway or airspace bottleneck?")
    assert method_route["name"] == "method"
    compared = follow_up("Compare Boston last year with LAX this year")
    assert compared["arguments"]["airports"] == ["Boston", "LAX"]
    assert compared["arguments"]["periods"] == ["last year", "this year"]
    assert follow_up("Compare congestion at LA and Santa Ana for 2024-06.") is None

    factory = _factory()
    with session_scope(factory) as session:
        _load(session)
        bos = compare(session, ["BOS"], "congestion", "2024-06")
        assert bos["airports"][0]["name"] == "Boston Logan"
        session.add(
            Observation(
                airport_id=bos["airports"][0]["id"],
                metric="enplanements",
                value=20000000,
                unit="passengers",
                period_start=datetime(clock.utcnow().year - 1, 1, 1),
                period_end=datetime(clock.utcnow().year, 1, 1),
                source="faa_enplanements",
                retrieved_at=datetime(2026, 10, 1),
                value_kind="measured",
                quality_flags="[]",
                coverage_note="FAA annual passenger boardings.",
            )
        )
        lax = compare(session, ["LAX"], "congestion", "2024-06")
        session.add(
            Observation(
                airport_id=lax["airports"][0]["id"],
                metric="arrival_delay_share",
                value=0.247,
                unit="share",
                period_start=datetime(clock.utcnow().year, 8, 1),
                period_end=datetime(clock.utcnow().year, 9, 1),
                source="bts_ontime",
                retrieved_at=datetime(2026, 10, 2),
                value_kind="measured",
                quality_flags="[]",
                coverage_note="BTS on-time.",
            )
        )
        session.add(
            Observation(
                airport_id=lax["airports"][0]["id"],
                metric="arrival_delay_mean_minutes",
                value=10.4,
                unit="minutes",
                period_start=datetime(clock.utcnow().year, 8, 1),
                period_end=datetime(clock.utcnow().year, 9, 1),
                source="bts_ontime",
                retrieved_at=datetime(2026, 10, 2),
                value_kind="measured",
                quality_flags="[]",
                coverage_note="BTS on-time.",
            )
        )
        session.commit()
        result = compare(
            session,
            ["Boston", "LAX"],
            "reported_history",
            periods=["last year", "this year"],
        )
        assert result["status"] == "compared"
        assert result["sides"][0]["period"] == str(clock.utcnow().year - 1)
        assert result["sides"][0]["enplanements_label"] == "20,000,000"
        assert result["sides"][0]["delay_label"] == "not loaded"
        assert result["sides"][1]["enplanements_label"] == "not loaded"
        assert "24.7% delayed, mean 10.4 min" in result["sides"][1]["delay_label"]
        assert "same complete month" not in result["reason"]
        text = explain_locally(result)
        assert "Boston Logan" in text and "Los Angeles International" in text
        assert "not loaded" in text

        conversation = Conversation(created_at=clock.utcnow())
        session.add(conversation)
        session.flush()
        prior = {
            "function": "screen",
            "status": "watchlist",
            "region": "New England",
            "project_type": "terminal expansion",
            "demand_window": [2020, 2025],
            "ranked": [],
            "airports": [
                {"name": "Tweed New Haven Airport", "iata": "HVN", "demand_score": 90.8},
                {"name": "Boston Logan", "iata": "BOS", "demand_score": 67.0},
            ],
            "profitability": {"claimed": False},
        }
        session.add(
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content="earlier",
                result_json=json.dumps(prior),
                created_at=clock.utcnow(),
            )
        )
        session.commit()
        recap = call_tool(session, "brief", {"conversation_id": conversation.id, "word_limit": 150})
        assert recap["word_count"] <= 150
        assert recap["summary"].count(" ") + 1 == recap["word_count"]
        for heading in ("Recommendation:", "Sources:", "Assumptions:", "Biggest uncertainty:"):
            assert heading in recap["summary"]
        assert "HVN" in recap["summary"] and "90.8" in recap["summary"]
        assert "Profitability is not claimed." in recap["summary"]
        fresh = Conversation(created_at=clock.utcnow())
        session.add(fresh)
        session.commit()
        opening = call_tool(session, "brief", {"conversation_id": fresh.id, "word_limit": 150})
        assert opening["word_count"] <= 150
        assert "Recommendation:" in opening["summary"]
        assert "Sources:" in opening["summary"]
        assert "Assumptions:" in opening["summary"]
        assert "Biggest uncertainty:" in opening["summary"]
        assert "no earlier scored answer" not in opening["summary"].lower()
        assert opening["reason"] == ""

        note = call_tool(session, "method", {"conversation_id": conversation.id})
        explained = explain_locally(note)
        assert "gate or stand" in explained
        assert "peak movements" in explained
        assert "airspace" in explained
        assert "C is not calculated" in explained
        assert "opportunity score" not in explained.lower()


async def _sleep(_seconds: float) -> None:
    return None
