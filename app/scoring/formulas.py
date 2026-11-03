DEMAND_WEIGHTS = {
    "long_term_growth": 0.40,
    "recent_growth": 0.25,
    "absolute_increase": 0.20,
    "growth_consistency": 0.15,
}


def cagr(start: float, end: float, intervals: int) -> float | None:
    if intervals <= 0 or start <= 0 or end <= 0:
        return None
    return (end / start) ** (1 / intervals) - 1


def normalize_ascending(values: dict[int, float]) -> dict[int, float] | None:
    if len(values) < 2:
        return None
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    count = len(ordered)
    scores: dict[int, float] = {}
    index = 0
    while index < count:
        end = index
        while end + 1 < count and ordered[end + 1][1] == ordered[index][1]:
            end += 1
        average_rank = ((index + 1) + (end + 1)) / 2
        normalized = 100 * (average_rank - 1) / (count - 1)
        for cursor in range(index, end + 1):
            scores[ordered[cursor][0]] = normalized
        index = end + 1
    return scores


def opportunity_score(demand: float | None, capacity: float | None, renovation: float | None) -> float | None:
    if demand is None or capacity is None or renovation is None:
        return None
    if demand < 0 or capacity < 0 or renovation < 0:
        return None
    return 100 * (demand / 100) ** 0.40 * (capacity / 100) ** 0.35 * (renovation / 100) ** 0.25


def demand_scores(series: dict[int, dict[int, float]]) -> dict:
    years = {year for values in series.values() for year in values}
    if not years:
        return {"scores": {}, "parts": {}, "reason": "No enplanement years are loaded.", "window": None, "cohort": []}
    latest = max(years)
    window = list(range(latest - 5, latest + 1))
    eligible: dict[int, dict[str, float]] = {}
    for airport_id, values in series.items():
        if any(year not in values for year in window):
            continue
        start = values[window[0]]
        end = values[window[-1]]
        previous = values[window[-2]]
        growth = cagr(start, end, 5)
        if growth is None or previous <= 0:
            continue
        changes = [values[window[index]] - values[window[index - 1]] for index in range(1, 6)]
        eligible[airport_id] = {
            "long_term_growth": growth,
            "recent_growth": end / previous - 1,
            "absolute_increase": end - start,
            "growth_consistency": sum(1 for change in changes if change > 0) / 5,
        }
    if len(eligible) < 2:
        return {
            "scores": {},
            "parts": {},
            "reason": (
                "The peer group with six comparable annual enplanements has fewer than two airports, "
                "so percentiles were not calculated."
            ),
            "window": window,
            "cohort": sorted(eligible),
        }
    normalized = {}
    for name in DEMAND_WEIGHTS:
        scored = normalize_ascending({airport_id: parts[name] for airport_id, parts in eligible.items()})
        if scored is None:
            return {
                "scores": {},
                "parts": {},
                "reason": "A demand component could not be normalized.",
                "window": window,
                "cohort": sorted(eligible),
            }
        normalized[name] = scored
    scores = {}
    parts = {}
    for airport_id in eligible:
        component_scores = {name: normalized[name][airport_id] for name in DEMAND_WEIGHTS}
        scores[airport_id] = sum(DEMAND_WEIGHTS[name] * component_scores[name] for name in DEMAND_WEIGHTS)
        parts[airport_id] = {"raw": eligible[airport_id], "normalized": component_scores}
    return {"scores": scores, "parts": parts, "reason": None, "window": window, "cohort": sorted(eligible)}
