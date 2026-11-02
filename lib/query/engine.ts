import type { Airport, Assumption, Catalog, Intent, Metric, QueryResult, ResultRow } from "@/lib/types"
import { stateLabel } from "@/lib/regions"
import { SCORE_WEIGHTS, compositeScore, percentileRanks, quantile, type ScoreInputs } from "@/lib/scoring/score"

const STRONG_FLOOR = 100_000

export function formatNumber(value: number, digits = 0): string {
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(value)
}

export function formatRatio(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

function missingScoreParts(airport: Airport): string[] {
  const missing: string[] = []
  if (airport.longRunways <= 0) missing.push("a paved runway of at least 5,000 feet")
  if (airport.ntad?.departures == null) missing.push("2024 domestic departures")
  if (airport.ntad?.enplanements == null || airport.ntad.enplanements <= 0) {
    missing.push("2024 domestic enplanements")
  }
  if (airport.faa?.yoy == null) missing.push("FAA enplanement change from 2023 to 2024")
  if (airport.ontime?.depDelay15Rate == null) missing.push("August 2026 departure delay rate")
  return missing
}

function scoreInputs(airport: Airport): ScoreInputs | null {
  if (missingScoreParts(airport).length > 0) return null
  return {
    pressure: airport.ntad!.departures! / airport.longRunways,
    scale: Math.log(airport.ntad!.enplanements!),
    yoy: airport.faa!.yoy!,
    delayRate: airport.ontime!.depDelay15Rate!,
    enplanements: airport.ntad!.enplanements!,
    departures: airport.ntad!.departures!,
    longRunways: airport.longRunways,
  }
}

function sourceAssumptions(extra: Assumption[] = []): Assumption[] {
  return [
    {
      label: "Domestic activity",
      detail:
        "Passengers, enplanements, departures, arrivals, freight, and mail are 2024 domestic totals for US carriers from the NTAD T-100 origin table. They do not include international flying.",
    },
    {
      label: "Enplanement change",
      detail:
        "Year-over-year change uses FAA commercial-service enplanements, calendar year 2024 divided by calendar year 2023, minus one. That FAA series is not the same definition as the domestic NTAD total.",
    },
    {
      label: "Delay",
      detail:
        "Delay rate, delay minutes, taxi-out, cancellations, and delay-cause minutes are from BTS Reporting Carrier On-Time Performance for August 2026 only. The file covers domestic reporting-carrier flights. Early departures are already zero in DepDelayMinutes. Blank delay-cause fields were not added as minutes.",
    },
    {
      label: "Runways",
      detail:
        "A long runway is one OurAirports lists as not closed, with a paved surface and length of at least 5,000 feet. A blank or unrecognized surface is not counted.",
    },
    {
      label: "Score",
      detail: `The capacity score is ${Math.round(SCORE_WEIGHTS.pressure * 100)}% departures per long runway, ${Math.round(SCORE_WEIGHTS.scale * 100)}% log of 2024 domestic enplanements, ${Math.round(SCORE_WEIGHTS.yoy * 100)}% FAA enplanement change, and ${Math.round(SCORE_WEIGHTS.delay * 100)}% August 2026 departure-delay rate. Percentiles are computed only inside the airports being compared. A higher delay rate raises the score. It is operational pressure in this formula, not a verdict on the airport. An airport missing any input does not get a score, and the missing input is not filled in.`,
    },
    {
      label: "Not a profit estimate",
      detail:
        "The score is not revenue, concession income, airline charges, construction cost, NPV, or IRR. Seats, gates, load factor, stage length, carriers, routes, and international segments are not in these files.",
    },
    ...extra,
  ]
}

function placeName(states: string[] | null): string {
  if (!states) return "the joined US set"
  if (states.length === 6 && ["CT", "ME", "MA", "NH", "RI", "VT"].every((state) => states.includes(state))) {
    return "New England"
  }
  if (states.length === 1) return stateLabel(states[0])
  return states.map(stateLabel).join(", ")
}

function filterAirports(catalog: Catalog, states: string[] | null): Airport[] {
  return Object.values(catalog.airports)
    .filter((airport) => (states ? states.includes(airport.state) : true))
    .filter((airport) => airport.faa || airport.ntad)
}

function rankPool(catalog: Catalog, states: string[] | null): Airport[] {
  return filterAirports(catalog, states).filter(
    (airport) => airport.faa != null || (airport.ntad?.enplanements ?? 0) >= STRONG_FLOOR,
  )
}

type Scored = {
  airport: Airport
  inputs: ScoreInputs | null
  missing: string[]
  pressurePercentile: number | null
  scalePercentile: number | null
  yoyPercentile: number | null
  delayPercentile: number | null
  score: number | null
  strong: boolean
}

function scoreSet(airports: Airport[]): Scored[] {
  const prelim = airports.map((airport) => ({
    airport,
    inputs: scoreInputs(airport),
    missing: missingScoreParts(airport),
  }))
  const complete = prelim.filter((item) => item.inputs)
  const pressure = percentileRanks(complete.map((item) => item.inputs!.pressure))
  const scale = percentileRanks(complete.map((item) => item.inputs!.scale))
  const yoy = percentileRanks(complete.map((item) => item.inputs!.yoy))
  const delay = percentileRanks(complete.map((item) => item.inputs!.delayRate))
  const byCode = new Map<string, Omit<Scored, "strong">>()
  complete.forEach((item, index) => {
    const score = compositeScore({
      pressurePercentile: pressure[index],
      scalePercentile: scale[index],
      yoyPercentile: yoy[index],
      delayPercentile: delay[index],
    })
    byCode.set(item.airport.iata, {
      airport: item.airport,
      inputs: item.inputs,
      missing: [],
      pressurePercentile: pressure[index],
      scalePercentile: scale[index],
      yoyPercentile: yoy[index],
      delayPercentile: delay[index],
      score,
    })
  })
  const scored = prelim.map((item) => {
    const found = byCode.get(item.airport.iata)
    if (found) return { ...found, strong: false }
    return {
      airport: item.airport,
      inputs: null,
      missing: item.missing,
      pressurePercentile: null,
      scalePercentile: null,
      yoyPercentile: null,
      delayPercentile: null,
      score: null,
      strong: false,
    }
  })
  const peers = scored.filter(
    (item) => item.score != null && item.inputs != null && item.inputs.enplanements >= STRONG_FLOOR,
  )
  const threshold = quantile(
    peers.map((item) => item.score!).sort((a, b) => a - b),
    0.75,
  )
  return scored.map((item) => ({
    ...item,
    strong:
      item.score != null &&
      item.inputs != null &&
      threshold != null &&
      item.inputs.enplanements >= STRONG_FLOOR &&
      item.inputs.yoy > 0 &&
      item.score >= threshold,
  }))
}

function airportRow(item: Scored, terminal: boolean): ResultRow {
  const airport = item.airport
  return {
    code: airport.iata,
    airport: `${airport.iata} ${airport.name}`,
    city: airport.city,
    state: airport.state,
    score: item.score == null ? null : Number(item.score.toFixed(1)),
    strong: item.strong,
    yoy: airport.faa?.yoy ?? null,
    departuresPerRunway: item.inputs ? Number(item.inputs.pressure.toFixed(0)) : null,
    delayRate: airport.ontime?.depDelay15Rate ?? null,
    domesticEnplanements: airport.ntad?.enplanements ?? null,
    faaEnplanements2024: airport.faa?.enplanements2024 ?? null,
    faaEnplanements2023: airport.faa?.enplanements2023 ?? null,
    departures: airport.ntad?.departures ?? null,
    longRunways: airport.longRunways,
    freightLb: airport.ntad?.freightLb ?? null,
    mail: airport.ntad?.mail ?? null,
    passengers: airport.ntad?.passengers ?? null,
    arrivals: airport.ntad?.arrivals ?? null,
    meanDelayMinutes: airport.ontime?.meanDepDelayMinutes ?? null,
    taxiOut: airport.ontime?.meanTaxiOutMinutes ?? null,
    cancellationRate: airport.ontime?.cancellationRate ?? null,
    missing: item.missing.join("; "),
    terminalAsked: terminal,
  }
}

const RANK_COLUMNS = [
  { key: "airport", label: "Airport" },
  { key: "state", label: "State" },
  { key: "score", label: "Score" },
  { key: "strong", label: "Strong" },
  { key: "yoy", label: "FAA change" },
  { key: "departuresPerRunway", label: "Departures / long runway" },
  { key: "delayRate", label: "Delay rate" },
  { key: "domesticEnplanements", label: "2024 domestic enplanements" },
]

function rankReply(place: string, items: Scored[], terminal: boolean, limitNote: string): string {
  const scored = items.filter((item) => item.score != null)
  const strong = items.filter((item) => item.strong)
  const peers = scored.filter((item) => item.inputs!.enplanements >= STRONG_FLOOR)
  const terminalLine = terminal
    ? " You asked about a terminal. I cannot separate a terminal constraint from a runway constraint, because seats, gates, and load factor are not in the files I have. This is the capacity score only."
    : ""
  const strongLine =
    strong.length === 0
      ? ` None are marked strong. A strong mark requires a score at or above the 75th percentile of the ${peers.length} airports here with at least ${formatNumber(STRONG_FLOOR)} domestic enplanements in 2024, and an FAA enplanement increase.`
      : ` Marked strong: ${strong.map((item) => item.airport.iata).join(", ")}. That mark means the score is at or above the 75th percentile of the ${peers.length} airports here with at least ${formatNumber(STRONG_FLOOR)} domestic enplanements in 2024, and FAA enplanements rose.`
  return `I ranked ${items.length} airports in ${place}. ${scored.length} have every score input, so percentiles are inside that group of ${scored.length}.${strongLine}${terminalLine}${limitNote}`
}

export function execute(catalog: Catalog, intent: Intent): QueryResult {
  if (intent.kind === "refuse") {
    return {
      title: "No answer in the loaded files",
      reply: intent.reason,
      speechText: intent.reason,
      refusal: true,
      columns: [],
      rows: [],
      assumptions: [
        {
          label: "What I looked for",
          detail: intent.lookedFor,
        },
      ],
      notes: [],
    }
  }

  if (intent.kind === "clarify") {
    const reply = `${intent.question} ${intent.options.map((option) => `${option.code} (${option.name}, ${option.city})`).join("; ")}.`
    return {
      title: "Which airport?",
      reply,
      speechText: intent.question,
      refusal: false,
      columns: [
        { key: "code", label: "Code" },
        { key: "airport", label: "Airport" },
        { key: "city", label: "City" },
        { key: "state", label: "State" },
      ],
      rows: intent.options.map((option) => ({
        code: option.code,
        airport: option.name,
        city: option.city,
        state: option.state,
      })),
      assumptions: [],
      notes: ["I did not pick one airport."],
    }
  }

  if (intent.kind === "compare") {
    if (intent.congestion || intent.metric === "delayRate" || intent.metric === "delayMinutes" || intent.metric === "taxiOut") {
      return congestionResult(catalog, intent.codes, intent.notes, intent.terminal)
    }
    return compareMetric(catalog, intent.codes, intent.metric, intent.notes, intent.terminal)
  }

  if (intent.kind === "airport" && intent.congestion && intent.metric === "delayRate") {
    return congestionResult(catalog, [intent.code], intent.notes, intent.terminal)
  }

  if (intent.kind === "airport") {
    return airportMetric(catalog, intent.code, intent.metric, intent.notes)
  }

  if (intent.groupBy === "state" && intent.metric !== "score") {
    return stateRollup(catalog, intent.states, intent.metric, intent.notes)
  }

  if (intent.groupBy === "state" && intent.metric === "score") {
    return {
      title: "No state score",
      reply:
        "I don't average the capacity score up to a state. I can list the airports, or sum freight, mail, passengers, or departures by state.",
      speechText: "I don't average the capacity score up to a state.",
      refusal: true,
      columns: [],
      rows: [],
      assumptions: sourceAssumptions(),
      notes: intent.notes,
    }
  }

  const pool = rankPool(catalog, intent.states)
  let items = scoreSet(pool)
  items.sort((a, b) => {
    if (a.score != null && b.score != null && a.score !== b.score) return b.score - a.score
    if (a.score != null && b.score == null) return -1
    if (a.score == null && b.score != null) return 1
    const aSize = a.airport.ntad?.enplanements ?? a.airport.faa?.enplanements2024 ?? 0
    const bSize = b.airport.ntad?.enplanements ?? b.airport.faa?.enplanements2024 ?? 0
    if (aSize !== bSize) return bSize - aSize
    return a.airport.iata.localeCompare(b.airport.iata)
  })
  if (intent.metric !== "score") {
    items = sortByMetric(items, intent.metric)
  }
  const ranked = items
  let shown = items
  let limitNote = ""
  if (intent.limit != null && shown.length > intent.limit) {
    shown = shown.slice(0, intent.limit)
    limitNote = ` The table shows the first ${intent.limit} of ${ranked.length}.`
  }
  const place = placeName(intent.states)
  const rows = shown.map((item) => airportRow(item, intent.terminal))
  const reply =
    intent.metric === "score"
      ? rankReply(place, ranked, intent.terminal, limitNote)
      : `I sorted ${ranked.length} airports in ${place} by ${metricLabel(intent.metric)}. Blank cells are not in the loaded files. I did not fill them.${limitNote}`

  return {
    title: intent.metric === "score" ? `Capacity rank, ${place}` : `${metricLabel(intent.metric)}, ${place}`,
    reply,
    speechText: reply.split(". ").slice(0, 2).join(". "),
    refusal: false,
    columns: intent.metric === "score" ? RANK_COLUMNS : metricColumns(intent.metric),
    rows,
    assumptions: sourceAssumptions(
      catalog.unmatched.faa.length
        ? [
            {
              label: "Omitted codes",
              detail: `These FAA locids did not join to an OurAirports iata or local code, so they are left out: ${catalog.unmatched.faa.join(", ")}.`,
            },
          ]
        : [],
    ),
    notes: intent.notes,
  }
}

function sortByMetric(items: Scored[], metric: Metric): Scored[] {
  const value = (item: Scored): number | null => {
    const airport = item.airport
    switch (metric) {
      case "freight":
        return airport.ntad?.freightLb ?? null
      case "mail":
        return airport.ntad?.mail ?? null
      case "passengers":
        return airport.ntad?.passengers ?? null
      case "enplanements":
        return airport.ntad?.enplanements ?? null
      case "departures":
        return airport.ntad?.departures ?? null
      case "arrivals":
        return airport.ntad?.arrivals ?? null
      case "yoy":
        return airport.faa?.yoy ?? null
      case "delayRate":
        return airport.ontime?.depDelay15Rate ?? null
      case "delayMinutes":
        return airport.ontime?.meanDepDelayMinutes ?? null
      case "taxiOut":
        return airport.ontime?.meanTaxiOutMinutes ?? null
      case "cancellationRate":
        return airport.ontime?.cancellationRate ?? null
      case "longRunways":
        return airport.longRunways
      case "elevation":
        return airport.elevationFt
      default:
        return item.score
    }
  }
  return [...items].sort((a, b) => {
    const av = value(a)
    const bv = value(b)
    if (av == null && bv == null) return a.airport.iata.localeCompare(b.airport.iata)
    if (av == null) return 1
    if (bv == null) return -1
    if (av !== bv) return bv - av
    return a.airport.iata.localeCompare(b.airport.iata)
  })
}

function metricLabel(metric: Metric): string {
  const labels: Record<Metric, string> = {
    score: "capacity score",
    enplanements: "2024 domestic enplanements",
    passengers: "2024 domestic passengers",
    departures: "2024 domestic departures",
    arrivals: "2024 domestic arrivals",
    freight: "2024 domestic freight pounds",
    mail: "2024 domestic mail",
    yoy: "FAA enplanement change, 2023 to 2024",
    delayRate: "August 2026 departure delay rate",
    delayMinutes: "August 2026 mean departure delay minutes",
    taxiOut: "August 2026 mean taxi-out minutes",
    cancellationRate: "August 2026 cancellation rate",
    longRunways: "long paved runways",
    delayCause: "August 2026 delay-cause minutes",
    elevation: "elevation",
  }
  return labels[metric]
}

function metricColumns(metric: Metric) {
  return [
    { key: "airport", label: "Airport" },
    { key: "state", label: "State" },
    { key: metric === "enplanements" ? "domesticEnplanements" : metricKey(metric), label: metricLabel(metric) },
  ]
}

function metricKey(metric: Metric): string {
  const keys: Record<Metric, string> = {
    score: "score",
    enplanements: "domesticEnplanements",
    passengers: "passengers",
    departures: "departures",
    arrivals: "arrivals",
    freight: "freightLb",
    mail: "mail",
    yoy: "yoy",
    delayRate: "delayRate",
    delayMinutes: "meanDelayMinutes",
    taxiOut: "taxiOut",
    cancellationRate: "cancellationRate",
    longRunways: "longRunways",
    delayCause: "carrierDelayMinutes",
    elevation: "elevation",
  }
  return keys[metric]
}

function airportMetric(catalog: Catalog, code: string, metric: Metric, notes: string[]): QueryResult {
  const airport = catalog.airports[code]
  if (!airport) {
    const reason = `I don't have ${code} in the joined public files, so I can't answer that.`
    return {
      title: "No answer in the loaded files",
      reply: reason,
      speechText: reason,
      refusal: true,
      columns: [],
      rows: [],
      assumptions: sourceAssumptions(),
      notes,
    }
  }
  if (metric === "score") {
    const national = scoreSet(filterAirports(catalog, null)).find((candidate) => candidate.airport.iata === code)
    if (!national || national.score == null || !national.inputs) {
      const reason = `I can't score ${code}. Missing: ${(national?.missing ?? missingScoreParts(airport)).join(", ")}.`
      return {
        title: `${code} score`,
        reply: reason,
        speechText: reason,
        refusal: true,
        columns: [],
        rows: [],
        assumptions: sourceAssumptions(),
        notes,
      }
    }
    const row = airportRow(national, false)
    const reply = `${code} scores ${row.score} against every joined airport that has all four score inputs. FAA enplanements changed ${formatRatio(airport.faa!.yoy!)} from 2023 to 2024. August 2026 departure delay rate was ${formatRatio(airport.ontime!.depDelay15Rate!)}. Domestic departures per long runway were ${formatNumber(national.inputs.pressure)}.`
    return {
      title: `${code} capacity score`,
      reply,
      speechText: reply,
      refusal: false,
      columns: RANK_COLUMNS,
      rows: [row],
      assumptions: sourceAssumptions(),
      notes,
    }
  }

  if (metric === "delayCause") {
    if (!airport.ontime) {
      const reason = `I don't have August 2026 reporting-carrier delay rows for ${code}.`
      return refusalResult(reason, notes)
    }
    const ontime = airport.ontime
    const reply = `August 2026 delay-cause minutes reported for domestic flights from ${code}: carrier ${formatNumber(ontime.carrierDelayMinutes)}, late aircraft ${formatNumber(ontime.lateAircraftDelayMinutes)}, national airspace ${formatNumber(ontime.nasDelayMinutes)}, weather ${formatNumber(ontime.weatherDelayMinutes)}, security ${formatNumber(ontime.securityDelayMinutes)}.`
    return {
      title: `${code} delay causes`,
      reply,
      speechText: reply,
      refusal: false,
      columns: [
        { key: "cause", label: "Cause column" },
        { key: "minutes", label: "Minutes" },
      ],
      rows: [
        { cause: "CarrierDelay", minutes: ontime.carrierDelayMinutes },
        { cause: "LateAircraftDelay", minutes: ontime.lateAircraftDelayMinutes },
        { cause: "NASDelay", minutes: ontime.nasDelayMinutes },
        { cause: "WeatherDelay", minutes: ontime.weatherDelayMinutes },
        { cause: "SecurityDelay", minutes: ontime.securityDelayMinutes },
      ],
      assumptions: sourceAssumptions(),
      notes,
    }
  }

  if (metric === "enplanements" || metric === "passengers") {
    const domestic = metric === "passengers" ? airport.ntad?.passengers : airport.ntad?.enplanements
    const faa = airport.faa?.enplanements2024
    if (domestic == null && faa == null) {
      return refusalResult(`I don't have a 2024 passenger or enplanement figure for ${code}.`, notes)
    }
    const bits = []
    if (domestic != null) {
      bits.push(
        metric === "passengers"
          ? `NTAD domestic passengers in 2024 were ${formatNumber(domestic)}`
          : `NTAD domestic enplanements in 2024 were ${formatNumber(domestic)}`,
      )
    }
    if (faa != null) bits.push(`FAA commercial-service enplanements in calendar year 2024 were ${formatNumber(faa)}`)
    const reply = `${code}, ${airport.name}. ${bits.join(". ")}. These are different series. I am not combining them.`
    return {
      title: `${code} passengers`,
      reply,
      speechText: reply,
      refusal: false,
      columns: [
        { key: "series", label: "Series" },
        { key: "value", label: "Value" },
      ],
      rows: [
        domestic == null
          ? null
          : {
              series: metric === "passengers" ? "NTAD 2024 domestic passengers" : "NTAD 2024 domestic enplanements",
              value: domestic,
            },
        faa == null ? null : { series: "FAA CY 2024 commercial enplanements", value: faa },
      ].filter(Boolean) as ResultRow[],
      assumptions: sourceAssumptions(),
      notes,
    }
  }

  const value = metricValue(airport, metric)
  if (value == null) {
    return refusalResult(`I don't have ${metricLabel(metric)} for ${code}.`, notes)
  }
  const reply = `${code}, ${airport.name}: ${metricLabel(metric)} is ${formatMetric(metric, value)}.`
  return {
    title: `${code} ${metricLabel(metric)}`,
    reply,
    speechText: reply,
    refusal: false,
    columns: [
      { key: "code", label: "Code" },
      { key: "metric", label: "Metric" },
      { key: "value", label: "Value" },
    ],
    rows: [{ code, metric: metricLabel(metric), value }],
    assumptions: sourceAssumptions(),
    notes,
  }
}

function metricValue(airport: Airport, metric: Metric): number | null {
  switch (metric) {
    case "freight":
      return airport.ntad?.freightLb ?? null
    case "mail":
      return airport.ntad?.mail ?? null
    case "departures":
      return airport.ntad?.departures ?? null
    case "arrivals":
      return airport.ntad?.arrivals ?? null
    case "yoy":
      return airport.faa?.yoy ?? null
    case "delayRate":
      return airport.ontime?.depDelay15Rate ?? null
    case "delayMinutes":
      return airport.ontime?.meanDepDelayMinutes ?? null
    case "taxiOut":
      return airport.ontime?.meanTaxiOutMinutes ?? null
    case "cancellationRate":
      return airport.ontime?.cancellationRate ?? null
    case "longRunways":
      return airport.longRunways
    case "elevation":
      return airport.elevationFt
    default:
      return null
  }
}

function formatMetric(metric: Metric, value: number): string {
  if (metric === "yoy" || metric === "delayRate" || metric === "cancellationRate") return formatRatio(value)
  if (metric === "delayMinutes" || metric === "taxiOut") return `${formatNumber(value, 1)} minutes`
  if (metric === "elevation") return `${formatNumber(value)} feet`
  if (metric === "freight") return `${formatNumber(value)} pounds`
  return formatNumber(value)
}

function compareMetric(
  catalog: Catalog,
  codes: string[],
  metric: Metric,
  notes: string[],
  terminal: boolean,
): QueryResult {
  if (metric === "score") {
    const scored = scoreSet(filterAirports(catalog, null)).filter((item) => codes.includes(item.airport.iata))
    const rows = scored.map((item) => airportRow(item, terminal))
    const reply = scored
      .map((item) =>
        item.score == null
          ? `${item.airport.iata} has no score. Missing: ${item.missing.join(", ")}.`
          : `${item.airport.iata} scores ${item.score.toFixed(1)}.`,
      )
      .join(" ")
    return {
      title: `Capacity score, ${codes.join(" and ")}`,
      reply: `${reply} Percentiles use every joined airport that has all four inputs, not just this pair.${terminal ? " This does not separate a terminal constraint from a runway constraint." : ""}`,
      speechText: reply,
      refusal: false,
      columns: RANK_COLUMNS,
      rows,
      assumptions: sourceAssumptions(),
      notes,
    }
  }
  const rows: ResultRow[] = []
  const lines: string[] = []
  for (const code of codes) {
    const airport = catalog.airports[code]
    if (!airport) {
      lines.push(`I don't have ${code}.`)
      continue
    }
    const value = metricValue(airport, metric)
    rows.push({ code, airport: airport.name, value })
    lines.push(value == null ? `I don't have ${metricLabel(metric)} for ${code}.` : `${code} is ${formatMetric(metric, value)}.`)
  }
  return {
    title: `${metricLabel(metric)}, ${codes.join(" and ")}`,
    reply: lines.join(" "),
    speechText: lines.join(" "),
    refusal: false,
    columns: [
      { key: "code", label: "Code" },
      { key: "airport", label: "Airport" },
      { key: "value", label: metricLabel(metric) },
    ],
    rows,
    assumptions: sourceAssumptions(),
    notes,
  }
}

function congestionResult(catalog: Catalog, codes: string[], notes: string[], terminal: boolean): QueryResult {
  const rows: ResultRow[] = []
  const lines: string[] = []
  for (const code of codes) {
    const airport = catalog.airports[code]
    if (!airport) {
      lines.push(`I don't have ${code} in the joined files.`)
      continue
    }
    const ontime = airport.ontime
    rows.push({
      code,
      airport: airport.name,
      state: airport.state,
      delayRate: ontime?.depDelay15Rate ?? null,
      meanDelayMinutes: ontime?.meanDepDelayMinutes ?? null,
      taxiOut: ontime?.meanTaxiOutMinutes ?? null,
      cancellationRate: ontime?.cancellationRate ?? null,
      departuresPerRunway:
        airport.ntad?.departures != null && airport.longRunways > 0
          ? Number((airport.ntad.departures / airport.longRunways).toFixed(0))
          : null,
      longRunways: airport.longRunways,
      domesticEnplanements: airport.ntad?.enplanements ?? null,
    })
    if (!ontime) {
      lines.push(`${code} has no August 2026 reporting-carrier delay rows.`)
    } else {
      lines.push(
        `${code} delay rate ${formatRatio(ontime.depDelay15Rate ?? 0)}, mean delay ${formatNumber(ontime.meanDepDelayMinutes ?? 0, 1)} minutes, taxi-out ${formatNumber(ontime.meanTaxiOutMinutes ?? 0, 1)} minutes, cancellations ${formatRatio(ontime.cancellationRate ?? 0)}.`,
      )
    }
  }
  const reply = `${lines.join(" ")} A lower delay rate is not evidence of spare capacity. Load factor is not in these files, so I am not using it. International flights are not in the August 2026 file.${terminal ? " This still does not identify a terminal constraint." : ""}`
  return {
    title: `Congestion, ${codes.join(" and ")}`,
    reply,
    speechText: lines.join(" "),
    refusal: rows.length === 0,
    columns: [
      { key: "code", label: "Code" },
      { key: "airport", label: "Airport" },
      { key: "delayRate", label: "Delay rate" },
      { key: "meanDelayMinutes", label: "Mean delay min" },
      { key: "taxiOut", label: "Taxi-out min" },
      { key: "cancellationRate", label: "Cancellation rate" },
      { key: "departuresPerRunway", label: "Departures / long runway" },
      { key: "longRunways", label: "Long runways" },
    ],
    rows,
    assumptions: sourceAssumptions(),
    notes,
  }
}

function stateRollup(catalog: Catalog, states: string[] | null, metric: Metric, notes: string[]): QueryResult {
  const airports = filterAirports(catalog, states)
  const totals = new Map<string, { value: number; count: number; missing: number }>()
  for (const airport of airports) {
    const value = metric === "enplanements" ? airport.ntad?.enplanements : metricValue(airport, metric)
    const bucket = totals.get(airport.state) ?? { value: 0, count: 0, missing: 0 }
    if (value == null) bucket.missing += 1
    else {
      bucket.value += value
      bucket.count += 1
    }
    totals.set(airport.state, bucket)
  }
  const rows = [...totals.entries()]
    .map(([state, bucket]) => ({
      state,
      value: bucket.count ? bucket.value : null,
      airports: bucket.count,
      missing: bucket.missing,
    }))
    .sort((a, b) => (b.value ?? -1) - (a.value ?? -1))
  const reply = `I summed ${metricLabel(metric)} by state for airports that have the figure. Airports without it are counted as missing, not as zero.`
  return {
    title: `${metricLabel(metric)} by state`,
    reply,
    speechText: reply,
    refusal: false,
    columns: [
      { key: "state", label: "State" },
      { key: "value", label: metricLabel(metric) },
      { key: "airports", label: "Airports with a value" },
      { key: "missing", label: "Airports missing it" },
    ],
    rows,
    assumptions: sourceAssumptions(),
    notes,
  }
}

function refusalResult(reason: string, notes: string[]): QueryResult {
  return {
    title: "No answer in the loaded files",
    reply: reason,
    speechText: reason,
    refusal: true,
    columns: [],
    rows: [],
    assumptions: sourceAssumptions(),
    notes,
  }
}
