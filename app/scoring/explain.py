from app.scoring.geography import TERRITORY_STATEMENT


def explain_locally(result: dict) -> str:
    lines = [f"Territory: {result.get('territory') or TERRITORY_STATEMENT}"]
    function = result.get("function")
    if function == "screen":
        names = result.get("state_names") or []
        lines.append(f"Region: {result.get('region')} ({_join(names)}).")
        lines.append(f"Peer set: {result.get('peer_set')}")
        lines.append(f"Project type: {result.get('project_type') or 'not specified'}.")
        lines.append(result.get("reason") or "")
        lines.extend(_watchlist(result))
        lines.extend(_ranked(result))
        lines.extend(_insufficient(result))
        if not result.get("ranked") and "no rank" not in (result.get("reason") or "").lower():
            lines.append("There is no rank.")
    elif function == "brief":
        return result.get("summary") or result.get("reason") or "There is no earlier scored answer to summarize."
    elif function == "method":
        return result.get("summary") or result.get("reason") or "There is no evidence rule to state."
    elif function == "compare" and result.get("sides"):
        lines.append(result.get("reason") or "")
        for side in result["sides"]:
            lines.append(
                f"{side.get('name')} ({side.get('period')}): FAA enplanements {side.get('enplanements_label')}. "
                f"BTS arrival delay {side.get('delay_label')}. Retrieved {side.get('retrieved_label')}."
            )
    elif function == "compare":
        lines.append("Metric: congestion.")
        period = result.get("period") or {}
        lines.append(f"Period: {period.get('label')}.")
        lines.append(result.get("reason") or "")
        lines.append("A live aircraft count was not used.")
        for airport in result.get("airports") or []:
            left = ", ".join(item["name"] for item in airport.get("left_out") or []) or "none"
            lines.append(f"{airport.get('query')} resolved to {airport.get('name')}. Airports left out: {left}.")
        for value in result.get("values") or []:
            lines.append(
                f"{value['name']}: {value['arrival_delay_share'] * 100:.1f} percent of arrivals "
                f"were delayed at least 15 minutes, mean {value['arrival_delay_mean_minutes']:.1f} minutes."
            )
    elif function == "share":
        airport = result.get("airport") or {}
        if airport.get("name"):
            lines.append(f"Airport: {airport['name']}.")
        lines.append(result.get("rule") or "Destination great-circle distance of at least 3000 statute miles.")
        label = (result.get("period") or {}).get("label")
        if label:
            lines.append(f"Period: {label}.")
        if result.get("excluded_destinations") is not None:
            lines.append(f"Excluded destinations: {result.get('excluded_destinations')}.")
        if result.get("percentage") is not None:
            lines.append(f"Percentage: {float(result['percentage']):.1f}.")
        lines.append(result.get("reason") or "")
        if result.get("sample"):
            lines.append(f"Sample: {result.get('sample')}.")
        lines.extend(_left_out(airport))
    elif function == "gap":
        airport = result.get("airport") or {}
        if airport.get("name"):
            lines.append(f"Airport: {airport['name']}.")
        missing = ", ".join(result.get("missing") or [])
        lines.append("Unmet demand is unknown.")
        lines.append(f"Missing: {missing}.")
        lines.append(result.get("reason") or "")
    else:
        lines.append(result.get("reason") or "There is no sound answer.")
    lines.extend(_evidence_lines(result))
    lines.append(_profit_line(result))
    return "\n".join(line for line in lines if line)


def _evidence_lines(result: dict) -> list[str]:
    lines = []
    for item in result.get("evidence") or []:
        if item.get("kind") != "project_cost":
            continue
        lines.append(
            "An analyst-supplied project cost is on file. It is not a measured API value. "
            "It is not a cash-flow forecast."
        )
        break
    return lines


def _ranked(result: dict) -> list[str]:
    rows = list(result.get("ranked") or [])
    if not rows:
        return []
    lines = [
        "Eligible pairs, highest S first. S is not profitability. Incremental passengers and movements are unknown."
    ]
    for item in rows:
        code = item.get("iata") or item.get("faa_lid") or ""
        lines.append(
            f"{item['name']} ({code}): D {item['demand_score']:.1f}, C {item['capacity_score']:.1f}, "
            f"R {item['renovation_fit']:.1f}, S {item['opportunity_score']:.1f}. {item.get('renovation_note') or ''}"
        )
    return lines


def _insufficient(result: dict) -> list[str]:
    rows = [item for item in result.get("airports") or [] if item.get("classification") == "insufficient_data"]
    if not rows:
        return []
    lines = ["Insufficient data:"]
    for item in rows:
        code = item.get("iata") or item.get("faa_lid") or ""
        lines.append(f"{item['name']} ({code}): {item.get('reason') or 'Foundational evidence is missing.'}")
    return lines


def _profit_line(result: dict) -> str:
    profit = result.get("profitability") or {}
    if profit.get("claimed") and profit.get("npv") is not None:
        return (
            f"Profitability: NPV {profit['npv']} {profit.get('currency')} "
            f"at discount rate {profit.get('discount_rate')}. {profit.get('reason')} "
            f"{profit.get('revenue_split') or ''}"
        )
    return "Profitability is not claimed."


def _watchlist(result: dict) -> list[str]:
    rows = [
        item
        for item in result.get("airports") or []
        if item.get("demand_score") is not None and "opportunity_score" not in item
    ]
    rows.sort(key=lambda item: item["demand_score"], reverse=True)
    if not rows:
        return []
    if result.get("ranked"):
        lines = ["Other airports are ordered by passenger growth and stay off the rank."]
    else:
        lines = ["Demand-growth order, strongest passenger growth first. This is not an opportunity rank."]
    for item in rows:
        code = item.get("iata") or item.get("faa_lid") or ""
        lines.append(f"{item['name']} ({code}): demand score {item['demand_score']:.1f}.")
    return lines


def _left_out(airport: dict) -> list[str]:
    left = ", ".join(item["name"] for item in airport.get("left_out") or []) or "none"
    if not airport.get("left_out") and airport.get("status") != "found":
        return []
    return [f"Airports left out: {left}."]


def _join(names: list[str]) -> str:
    if not names:
        return "none"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + ", and " + names[-1]
