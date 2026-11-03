def cash_flow_schedule(text: str) -> dict | None:
    headers = {}
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lower() == "cash flows:":
            continue
        if ":" in line and "," not in line.split(":", 1)[0]:
            key, value = line.split(":", 1)
            headers[key.strip().lower().replace(" ", "_")] = value.strip()
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 7:
            return None
        rows.append(parts)
    required = ("discount_rate", "currency", "inflation", "perspective")
    if any(not headers.get(name) for name in required) or not rows:
        return None
    try:
        rate = float(headers["discount_rate"])
    except ValueError:
        return None
    if rate <= -1:
        return None
    flows = []
    years = []
    for parts in rows:
        if len(parts[0]) != 4 or not parts[0].isdigit():
            return None
        try:
            amounts = [float(part) for part in parts[1:]]
        except ValueError:
            return None
        year = int(parts[0])
        if year in years:
            return None
        years.append(year)
        aeronautical, non_aeronautical, operating, taxes, capex, working_capital = amounts
        flows.append(aeronautical + non_aeronautical - operating - taxes - capex - working_capital)
    first = years[0]
    npv = sum(flow / (1 + rate) ** (year - first) for year, flow in zip(years, flows))
    return {
        "claimed": True,
        "npv": round(npv, 2),
        "currency": headers["currency"],
        "discount_rate": rate,
        "inflation": headers["inflation"],
        "perspective": headers["perspective"],
        "revenue_split": "Aeronautical and non-aeronautical revenue are separate inputs.",
        "reason": "NPV uses the analyst-supplied cash-flow schedule. It is not a measured API value.",
    }


def capacity_mapping_score(text: str) -> float | None:
    score_text, separator, note = text.partition("|")
    if not separator or not note.strip():
        return None
    try:
        score = float(score_text.strip())
    except ValueError:
        return None
    if score < 0 or score > 100:
        return None
    return score
