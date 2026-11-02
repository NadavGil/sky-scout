import type { Airport, Catalog, Intent, Metric } from "@/lib/types"
import { REGION_ORDER, REGIONS, STATE_NAMES } from "@/lib/regions"

const CODE_STOP = new Set(
  "AND,THE,FOR,TOP,ANY,NOT,HOW,WHY,ALL,OUT,PER,NEW,ARE,YOU,CAN,HAS,ITS,LOW,AIR,WHO,DID,WAS,BUT,NOW,SEE,MAY,DAY,ASK,GET,HAD,HER,HIS,OUR,SHE,TOO,USE,WAY,WHAT,SHOW,ONLY,JUST,FROM,WITH,THAT,THIS,HAVE,INTO,OVER,THAN,THEN,THEM,THEY,YEAR,RATE,MILE,MILES".split(
    ",",
  ),
)

const ALIASES: Array<{ phrase: string; code: string; note?: string }> = [
  { phrase: "los angeles", code: "LAX" },
  { phrase: "santa ana", code: "SNA" },
  { phrase: "john wayne", code: "SNA" },
  { phrase: "orange county", code: "SNA" },
  { phrase: "san francisco", code: "SFO" },
  { phrase: "anchorage", code: "ANC" },
  { phrase: "fort worth", code: "DFW" },
  { phrase: "dallas love", code: "DAL" },
  { phrase: "new england", code: "" },
]

const LA_NEARBY = ["BUR", "LGB", "ONT", "SNA", "VNY", "OXR"]

type Found = {
  codes: string[]
  ambiguous: Airport[][]
  notes: string[]
  consumed: string
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

function hasPhrase(text: string, phrase: string): boolean {
  return new RegExp(`\\b${escapeRegExp(phrase)}\\b`, "i").test(text)
}

function stripPhrase(text: string, phrase: string): string {
  return text.replace(new RegExp(`\\b${escapeRegExp(phrase)}\\b`, "ig"), " ")
}

function findPlaces(text: string): { states: string[] | null; label: string | null; rest: string } {
  let rest = text
  for (const name of REGION_ORDER) {
    if (hasPhrase(rest, name)) {
      return { states: REGIONS[name], label: name, rest: stripPhrase(rest, name) }
    }
  }
  const stateNames = Object.keys(STATE_NAMES).sort((a, b) => b.length - a.length)
  for (const name of stateNames) {
    if (hasPhrase(rest, name)) {
      return { states: [STATE_NAMES[name]], label: name, rest: stripPhrase(rest, name) }
    }
  }
  return { states: null, label: null, rest }
}

function findAirports(text: string, catalog: Catalog): Found {
  let rest = text.toLowerCase()
  const codes: string[] = []
  const notes: string[] = []
  const ambiguous: Airport[][] = []
  const add = (code: string) => {
    if (catalog.airports[code] && !codes.includes(code)) codes.push(code)
  }

  if (hasPhrase(rest, "la") || hasPhrase(rest, "los angeles")) {
    add("LAX")
    rest = stripPhrase(stripPhrase(rest, "los angeles"), "la")
    const others = LA_NEARBY.filter((code) => catalog.airports[code]).map((code) => catalog.airports[code].iata)
    if (others.length) {
      notes.push(`LA means LAX. Not included: ${others.join(", ")}.`)
    }
  }

  for (const alias of ALIASES) {
    if (!alias.code || !hasPhrase(rest, alias.phrase)) continue
    add(alias.code)
    rest = stripPhrase(rest, alias.phrase)
    if (alias.note) notes.push(alias.note)
  }

  const airports = Object.values(catalog.airports)
  const names = airports
    .map((airport) => ({ airport, name: airport.name.toLowerCase() }))
    .filter((item) => item.name.length >= 12)
    .sort((a, b) => b.name.length - a.name.length)
  for (const item of names) {
    if (hasPhrase(rest, item.name)) {
      add(item.airport.iata)
      rest = stripPhrase(rest, item.name)
    }
  }

  const byCity = new Map<string, Airport[]>()
  for (const airport of airports) {
    const city = airport.city.toLowerCase().trim()
    if (city.length < 4) continue
    const list = byCity.get(city) ?? []
    list.push(airport)
    byCity.set(city, list)
  }
  const cities = [...byCity.keys()].sort((a, b) => b.length - a.length)
  for (const city of cities) {
    if (!hasPhrase(rest, city)) continue
    const matches = byCity.get(city)!
    const fresh = matches.filter((airport) => !codes.includes(airport.iata))
    if (fresh.length === 1) add(fresh[0].iata)
    else if (fresh.length > 1) ambiguous.push(fresh)
    rest = stripPhrase(rest, city)
  }

  const tokens = rest.toUpperCase().match(/\b[A-Z]{3}\b/g) ?? []
  for (const token of tokens) {
    if (CODE_STOP.has(token)) continue
    if (catalog.airports[token]) add(token)
  }

  return { codes, ambiguous, notes, consumed: rest }
}

function detectMetric(text: string): Metric | null {
  const lower = text.toLowerCase()
  if (/\bdelay causes?\b|\bwhy .*delay\b|\bweather delay\b|\bnas delay\b/.test(lower)) return "delayCause"
  if (/\btaxi[- ]?out\b|\btaxi out\b/.test(lower)) return "taxiOut"
  if (/\bcancell/.test(lower)) return "cancellationRate"
  if (/\bcongest|\bdelay rate\b|\bon[- ]?time\b|\bdelayed\b/.test(lower)) return "delayRate"
  if (/\bmean delay\b|\bdelay minutes\b|\baverage delay\b/.test(lower)) return "delayMinutes"
  if (/\bfreight\b|\bcargo\b/.test(lower)) return "freight"
  if (/\bmail\b/.test(lower)) return "mail"
  if (/\b(year[- ]over[- ]year|yoy|growth|percent change|enplanement change)\b/.test(lower)) return "yoy"
  if (/\bdepartures?\b|\bflights out\b|\btakeoffs?\b/.test(lower)) return "departures"
  if (/\barrivals?\b/.test(lower)) return "arrivals"
  if (/\brunways?\b/.test(lower)) return "longRunways"
  if (/\belevation\b/.test(lower)) return "elevation"
  if (/\bpassengers?\b/.test(lower)) return "passengers"
  if (/\benplanements?\b/.test(lower)) return "enplanements"
  if (/\b(score|rank|ranking|candidates?|capacity)\b/.test(lower)) return "score"
  return null
}

function refusal(text: string, airportsFound: string[]): Intent | null {
  let residue = text.toLowerCase()
  for (const phrase of airportsFound) residue = stripPhrase(residue, phrase)
  residue = residue.replace(/\binternational airport\b/g, " ")
  if (/\blong[-\s]?haul\b|\bstage length\b/.test(residue)) {
    return {
      kind: "refuse",
      lookedFor: "departures by nonstop stage length",
      reason:
        "I don't have stage length, so I can't give a long-haul share. The files I have don't break departures out by distance.",
    }
  }
  if (/\bunmet\b|\bspill\b|\bunserved demand\b|\bturned[- ]away\b/.test(residue)) {
    return {
      kind: "refuse",
      lookedFor: "seats, bookings, or turned-away passengers",
      reason:
        "I don't have seats, bookings, or turned-away passengers, so I can't estimate unmet demand.",
    }
  }
  if (/\bload factor\b|\bseats\b/.test(residue)) {
    return {
      kind: "refuse",
      lookedFor: "seats or load factor",
      reason: "I don't have seats or load factor in the files loaded for this desk.",
    }
  }
  if (/\b(airline|airlines|carrier|carriers)\b/.test(residue)) {
    return {
      kind: "refuse",
      lookedFor: "activity by airline",
      reason: "I don't have a carrier breakdown in the files loaded for this desk.",
    }
  }
  if (/\b(routes?|destinations?|nonstops? to|flights to)\b/.test(residue)) {
    return {
      kind: "refuse",
      lookedFor: "origin and destination pairs",
      reason: "I don't have route or destination pairs in the files loaded for this desk.",
    }
  }
  if (/\binternational\b/.test(residue)) {
    return {
      kind: "refuse",
      lookedFor: "international segments",
      reason:
        "I don't have international segments. The activity file I have is domestic US-carrier traffic for 2024.",
    }
  }
  if (/\b(fares?|ticket price|construction|capex| NPV|irr|cost to build|gates?|slots?)\b/.test(text.toLowerCase())) {
    return {
      kind: "refuse",
      lookedFor: "fares, gates, slots, or construction cost",
      reason: "I don't have fares, gate counts, slots, or construction cost in the files loaded for this desk.",
    }
  }
  if (/\b(january|february|march|april|may|june|july|september|october|november|december)\b/.test(residue) && /\bdelay|on[- ]?time|cancel|taxi/.test(residue)) {
    return {
      kind: "refuse",
      lookedFor: "a delay month other than August 2026",
      reason: "The only delay file I have is August 2026. I won't use another month.",
    }
  }
  const year = residue.match(/\b(19|20)\d{2}\b/)
  if (year && !["2023", "2024", "2026"].includes(year[0])) {
    return {
      kind: "refuse",
      lookedFor: `figures for ${year[0]}`,
      reason: `I don't have a ${year[0]} file loaded. I have FAA enplanements for 2023 and 2024, domestic activity for 2024, and delays for August 2026.`,
    }
  }
  return null
}

function limitFrom(text: string): number | null {
  const match = text.match(/\btop\s+(\d{1,2})\b/i)
  if (!match) return null
  return Number(match[1])
}

export function interpret(message: string, catalog: Catalog, previous: Intent | null): Intent {
  const text = message.trim().replace(/\s+/g, " ")
  if (!text) {
    return {
      kind: "refuse",
      lookedFor: "a question",
      reason: "Ask about a field I have, such as freight, delay, enplanements, or the capacity score. I won't guess.",
    }
  }

  const places = findPlaces(text)
  const found = findAirports(places.rest, catalog)
  const blocked = refusal(text, found.codes.map((code) => catalog.airports[code]?.name.toLowerCase() ?? code))
  if (blocked) return blocked

  if (found.ambiguous.length && found.codes.length === 0) {
    const options = found.ambiguous[0].map((airport) => ({
      code: airport.iata,
      name: airport.name,
      city: airport.city,
      state: airport.state,
    }))
    return {
      kind: "clarify",
      question: "Which airport do you mean?",
      options,
    }
  }

  const metric = detectMetric(text)
  const terminal = /\bterminal\b|\bconcourse\b/.test(text.toLowerCase())
  const congestion = /\bcongest|\bdelay\b|\bon[- ]?time\b/.test(text.toLowerCase())
  const compare = /\b(compare|comparison|versus|vs\.?)\b/i.test(text) || (found.codes.length >= 2 && /\band\b/i.test(text))
  const rank = /\b(which airports|rank|ranking|candidates?|best|strongest|top\b|list|where should)\b/i.test(text)
  const groupBy = /\bby state\b/i.test(text) ? "state" : "airport"
  const follow =
    /^(what about|how about|and\b|now\b|only\b|just\b|show\b|break\b)/i.test(text) &&
    previous &&
    previous.kind !== "refuse" &&
    previous.kind !== "clarify"

  if (follow && previous && (previous.kind === "rank" || previous.kind === "compare" || previous.kind === "airport")) {
    const nextMetric = metric ?? previous.metric
    if (found.codes.length >= 2) {
      return {
        kind: "compare",
        codes: found.codes,
        metric: nextMetric,
        terminal,
        congestion,
        notes: found.notes,
      }
    }
    if (found.codes.length === 1 && previous.kind === "compare") {
      const other = previous.codes.find((code) => code !== found.codes[0]) ?? previous.codes[0]
      return {
        kind: "compare",
        codes: [found.codes[0], other],
        metric: nextMetric,
        terminal,
        congestion: congestion || previous.congestion,
        notes: found.notes,
      }
    }
    if (found.codes.length === 1) {
      return {
        kind: "airport",
        code: found.codes[0],
        metric: nextMetric,
        terminal,
        congestion,
        notes: found.notes,
      }
    }
    if (previous.kind === "rank") {
      return {
        kind: "rank",
        states: places.states ?? previous.states,
        metric: nextMetric,
        terminal: terminal || previous.terminal,
        congestion: congestion || previous.congestion,
        limit: limitFrom(text) ?? previous.limit,
        groupBy,
        notes: [...previous.notes, ...found.notes],
      }
    }
    if (previous.kind === "airport" && metric) {
      return { ...previous, metric, notes: previous.notes }
    }
    if (previous.kind === "compare" && metric) {
      return { ...previous, metric, congestion: congestion || previous.congestion }
    }
  }

  if (compare) {
    if (found.codes.length < 2) {
      if (found.ambiguous.length) {
        return {
          kind: "clarify",
          question: "Which airports should I compare?",
          options: found.ambiguous.flat().map((airport) => ({
            code: airport.iata,
            name: airport.name,
            city: airport.city,
            state: airport.state,
          })),
        }
      }
      return {
        kind: "refuse",
        lookedFor: "two airports to compare",
        reason: "I need two airports I can find in the loaded files. I won't pick the second one.",
      }
    }
    return {
      kind: "compare",
      codes: found.codes.slice(0, 4),
      metric: metric ?? (congestion ? "delayRate" : "score"),
      terminal,
      congestion,
      notes: found.notes,
    }
  }

  if (rank || (places.states && found.codes.length === 0)) {
    return {
      kind: "rank",
      states: places.states,
      metric: metric ?? "score",
      terminal,
      congestion,
      limit: places.states ? limitFrom(text) : (limitFrom(text) ?? 15),
      groupBy,
      notes: found.notes,
    }
  }

  if (found.codes.length === 1) {
    return {
      kind: "airport",
      code: found.codes[0],
      metric: metric ?? (congestion ? "delayRate" : "score"),
      terminal,
      congestion,
      notes: found.notes,
    }
  }

  if (found.codes.length > 1) {
    return {
      kind: "compare",
      codes: found.codes.slice(0, 4),
      metric: metric ?? "score",
      terminal,
      congestion,
      notes: found.notes,
    }
  }

  return {
    kind: "refuse",
    lookedFor: text,
    reason:
      "I couldn't match that to an airport, a place, or a field I have. I won't guess. Try an airport code, a state, or a metric such as freight, delay, or the capacity score.",
  }
}
