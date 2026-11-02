import type { Airport, Catalog, Intent, Metric } from "@/lib/types"
import {
  AMBIGUOUS_STATE_ABBREVIATIONS,
  REGION_ORDER,
  REGIONS,
  STATE_ABBREVIATIONS,
  STATE_NAMES,
  stateLabel,
} from "@/lib/regions"

const CODE_STOP = new Set(
  "AND,THE,FOR,TOP,ANY,NOT,HOW,WHY,ALL,OUT,PER,ARE,YOU,CAN,HAS,ITS,LOW,AIR,WHO,DID,WAS,BUT,NOW,MAY,ASK,GET,HAD,HER,HIS,OUR,SHE,TOO,USE,WAY,WHAT,SHOW,ONLY,JUST,FROM,WITH,THAT,THIS,HAVE,INTO,OVER,THAN,THEN,THEM,THEY,YEAR,RATE,MILE,MILES".split(
    ",",
  ),
)

// Lowercase words that are also airport codes. They count only as an explicit
// capital code, or after a place preposition when they are not in IDIOM_CODES.
const ENGLISH_CODE_WORDS = new Set(
  "ARC,ART,BED,BET,BID,BOW,CAR,DAB,DAY,DEN,EAR,EAT,ELM,FAR,FAT,FRY,GNU,GUM,HOB,HOP,HOT,HUT,IRK,LAW,LIT,LOT,MOB,MOD,NEW,NIP,OAK,OFF,PIE,PIT,PUB,RAP,ROW,RUT,SAC,SAD,SAT,SAW,SEA,SEE,SIT,SOW,SUN,YAK".split(
    ",",
  ),
)

const IDIOM_CODES = new Set(["FAR", "SUN", "SEA", "DAY", "NEW", "SEE", "ART", "BED", "HOT", "OFF", "ROW", "LOT", "CAR"])

const ALIASES: Array<{ phrase: string; code: string; note?: string }> = [
  { phrase: "los angeles", code: "LAX" },
  { phrase: "santa ana", code: "SNA" },
  { phrase: "john wayne", code: "SNA" },
  { phrase: "orange county", code: "SNA" },
  { phrase: "san francisco", code: "SFO" },
  { phrase: "anchorage", code: "ANC" },
  { phrase: "fort worth", code: "DFW" },
  { phrase: "dallas love", code: "DAL" },
  { phrase: "o'hare", code: "ORD" },
  { phrase: "ohare", code: "ORD" },
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

const longerNamesByCatalog = new WeakMap<Catalog, string[]>()

function longerPlaceNames(catalog: Catalog): string[] {
  const cached = longerNamesByCatalog.get(catalog)
  if (cached) return cached
  const names = new Set<string>(Object.keys(STATE_NAMES))
  for (const airport of Object.values(catalog.airports)) {
    const city = airport.city.toLowerCase().trim()
    if (city.length >= 4) names.add(city)
  }
  const list = [...names]
  longerNamesByCatalog.set(catalog, list)
  return list
}

function shadowsLongerPlace(text: string, phrase: string, longerNames: string[]): boolean {
  const match = new RegExp(`\\b${escapeRegExp(phrase)}\\b`, "i").exec(text)
  if (!match || match.index == null) return false
  const from = text.slice(match.index)
  return longerNames.some(
    (name) => name.length > phrase.length && new RegExp(`^${escapeRegExp(name)}\\b`, "i").test(from),
  )
}

function findStateAbbreviations(text: string): string[] {
  const found: string[] = []
  for (const match of text.matchAll(/\b([A-Za-z]{2})\b/g)) {
    const raw = match[1]
    const code = raw.toUpperCase()
    if (!STATE_ABBREVIATIONS.has(code) || code === "LA") continue
    if (AMBIGUOUS_STATE_ABBREVIATIONS.has(code) && raw !== code) continue
    if (!found.includes(code)) found.push(code)
  }
  return found
}

function findPlaces(text: string, catalog: Catalog): { states: string[] | null; label: string | null; rest: string } {
  const stateNames = Object.keys(STATE_NAMES).sort((a, b) => b.length - a.length)
  for (const name of stateNames) {
    if (hasPhrase(text, name)) {
      return { states: [STATE_NAMES[name]], label: name, rest: stripPhrase(text, name) }
    }
  }
  const longer = longerPlaceNames(catalog)
  for (const name of REGION_ORDER) {
    if (!hasPhrase(text, name) || shadowsLongerPlace(text, name, longer)) continue
    return { states: REGIONS[name], label: name, rest: stripPhrase(text, name) }
  }
  const abbreviations = findStateAbbreviations(text)
  if (abbreviations.length) {
    let rest = text
    for (const code of abbreviations) rest = stripPhrase(rest, code)
    const label = abbreviations.length === 1 ? stateLabel(abbreviations[0]) : abbreviations.join(" ")
    return { states: abbreviations, label, rest }
  }
  return { states: null, label: null, rest: text }
}

function canonicalCode(catalog: Catalog, code: string): string | null {
  const alias = catalog.aliases?.[code]
  if (alias && catalog.airports[alias]) return alias
  if (catalog.airports[code]) return code
  return null
}

function followsPlacePreposition(text: string, token: string): boolean {
  return new RegExp(`\\b(?:at|from|to|via|through|for|in)\\s+${escapeRegExp(token)}\\b`, "i").test(text)
}

function findAirports(text: string, catalog: Catalog, original: string): Found {
  let rest = text.toLowerCase()
  const codes: string[] = []
  const notes: string[] = []
  const ambiguous: Airport[][] = []
  const add = (code: string) => {
    const canonical = canonicalCode(catalog, code)
    if (!canonical || codes.includes(canonical)) return
    codes.push(canonical)
    if (canonical !== code) notes.push(`${code} is the same airport as ${canonical} in this file.`)
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
    const explicit = new RegExp(`\\b${token}\\b`).test(original)
    if (!explicit && ENGLISH_CODE_WORDS.has(token)) {
      if (IDIOM_CODES.has(token) || !followsPlacePreposition(rest, token.toLowerCase())) continue
    }
    add(token)
  }

  return { codes, ambiguous, notes, consumed: rest }
}

function detectMetric(text: string): Metric | null {
  const lower = text.toLowerCase()
  if (/\bdelay causes?\b|\bwhy\b.*\bdelays?\b|\bweather delays?\b|\bnas delays?\b/.test(lower)) return "delayCause"
  if (/\btaxi[- ]?out\b|\btaxi out\b/.test(lower)) return "taxiOut"
  if (/\bcancell/.test(lower)) return "cancellationRate"
  if (/\bcongest|\bdelay rates?\b|\bdelays?\b|\bon[- ]?time\b|\bdelayed\b/.test(lower)) return "delayRate"
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
  if (/\bhow far\b|\bdistance between\b|\bmiles from\b|\bnautical miles\b/.test(residue)) {
    return {
      kind: "refuse",
      lookedFor: "distance between airports",
      reason: "I don't have distances between airports. Stage length is not in the files loaded for this desk.",
    }
  }
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
  const month =
    residue.match(/\b(?:january|february|march|april|june|july|september|october|november|december)\b/) ??
    residue.match(/\bmay\b(?!\s+i\b)/)
  if (month && /\bdelays?\b|\bdelay\b|on[- ]?time|cancel|taxi/.test(residue)) {
    return {
      kind: "refuse",
      lookedFor: "a delay month other than August 2026",
      reason: "The only delay file I have is August 2026. I won't use another month.",
    }
  }
  const years = [...residue.matchAll(/\b(?:19|20)\d{2}\b/g)].map((match) => match[0])
  const unsupported = years.find((year) => !["2023", "2024", "2026"].includes(year))
  if (unsupported) {
    return {
      kind: "refuse",
      lookedFor: `figures for ${unsupported}`,
      reason: `I don't have a ${unsupported} file loaded. I have FAA enplanements for 2023 and 2024, domestic activity for 2024, and delays for August 2026.`,
    }
  }
  return null
}

const METRIC_YEARS: Record<Metric, number[] | null> = {
  passengers: [2024],
  freight: [2024],
  mail: [2024],
  departures: [2024],
  arrivals: [2024],
  enplanements: [2023, 2024],
  yoy: [2023, 2024],
  delayRate: [2026],
  delayMinutes: [2026],
  taxiOut: [2026],
  cancellationRate: [2026],
  delayCause: [2026],
  score: null,
  longRunways: null,
  elevation: null,
}

function yearsIn(text: string): number[] {
  return [...text.matchAll(/\b(?:19|20)\d{2}\b/g)].map((match) => Number(match[0]))
}

function yearIntent(text: string, metric: Metric): { year?: number; refusal: Intent | null } {
  const years = [...new Set(yearsIn(text))]
  if (years.length === 0) return { refusal: null }
  if (years.length > 1) {
    return {
      refusal: {
        kind: "refuse",
        lookedFor: `figures for ${years.join(" and ")}`,
        reason: "I can answer one year at a time. Name a single year I have, or leave the year out.",
      },
    }
  }
  const year = years[0]
  if (metric === "score") {
    return {
      refusal: {
        kind: "refuse",
        lookedFor: `a capacity score for ${year}`,
        reason: `I can't give a capacity score for ${year} alone. The score uses 2024 domestic traffic, the FAA change from 2023 to 2024, and August 2026 delays together. Ask for the score without a year, or ask for one of those fields.`,
      },
    }
  }
  const allowed = METRIC_YEARS[metric]
  if (allowed == null) return { year, refusal: null }
  if (!allowed.includes(year)) {
    const have = allowed.join(" and ")
    return {
      refusal: {
        kind: "refuse",
        lookedFor: `${metric} for ${year}`,
        reason: `I don't have that field for ${year}. I have it for ${have}. I won't substitute another year.`,
      },
    }
  }
  return { year, refusal: null }
}

function palmBeachRefusal(text: string, catalog: Catalog): Intent | null {
  if (/county park|glades/i.test(text)) return null
  if (!/\bpbi\b|\bpalm beach\b/i.test(text)) return null
  const unmatched = catalog.unmatched.faa.includes("PBI")
  return {
    kind: "refuse",
    lookedFor: "Palm Beach International, FAA locid PBI",
    reason: unmatched
      ? "PBI is in the FAA enplanement file and did not join to an OurAirports iata or local code, so I left it out. I won't substitute another Palm Beach field."
      : "I don't have PBI in the joined files, and I won't substitute another Palm Beach field.",
  }
}

function limitFrom(text: string): number | null {
  const match = text.match(/\btop\s+(\d{1,2})\b/i)
  if (!match) return null
  return Number(match[1])
}

function clarifyOptions(groups: Airport[][]) {
  const seen = new Set<string>()
  const options = []
  for (const airport of groups.flat()) {
    if (seen.has(airport.iata)) continue
    seen.add(airport.iata)
    options.push({ code: airport.iata, name: airport.name, city: airport.city, state: airport.state })
  }
  return options
}

function placeIsInsideAirport(label: string | null, airports: Airport[]): boolean {
  if (!label) return false
  return airports.some((airport) => hasPhrase(`${airport.city} ${airport.name}`, label))
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

  const omitted = palmBeachRefusal(text, catalog)
  if (omitted) return omitted

  const places = findPlaces(text, catalog)
  const found = findAirports(places.rest, catalog, text)
  const blocked = refusal(text, found.codes.map((code) => catalog.airports[code]?.name.toLowerCase() ?? code))
  if (blocked) return blocked

  if (found.ambiguous.length) {
    const known = found.codes.length ? ` I already found ${found.codes.join(", ")}.` : ""
    return {
      kind: "clarify",
      question: `Which airport do you mean?${known}`,
      options: clarifyOptions(found.ambiguous),
    }
  }

  const metric = detectMetric(text)
  const terminal = /\bterminal\b|\bconcourse\b/.test(text.toLowerCase())
  const congestion = /\bcongest|\bdelays?\b|\bdelay\b|\bon[- ]?time\b/.test(text.toLowerCase())
  const compare = /\b(compare|comparison|versus|vs\.?)\b/i.test(text) || (found.codes.length >= 2 && /\band\b/i.test(text))
  const rank = /\b(which airports|rank|ranking|candidates?|best|strongest|top\b|list|where should)\b/i.test(text)
  const groupBy = /\bby state\b/i.test(text) ? "state" : "airport"
  const resolvedMetric = metric ?? (congestion ? "delayRate" : "score")
  const dated = yearIntent(text, metric ?? resolvedMetric)
  if (dated.refusal) return dated.refusal
  const year = dated.year
  const limit = limitFrom(text)
  if (limit === 0) {
    return {
      kind: "refuse",
      lookedFor: "a positive top count",
      reason: "A top count has to be at least 1. I didn't run the ranking.",
    }
  }
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
        year,
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
        year,
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
        year,
      }
    }
    if (previous.kind === "rank") {
      return {
        kind: "rank",
        states: places.states ?? previous.states,
        metric: nextMetric,
        terminal: terminal || previous.terminal,
        congestion: congestion || previous.congestion,
        limit: limit ?? previous.limit,
        groupBy,
        notes: [...previous.notes, ...found.notes],
        year,
      }
    }
    if (previous.kind === "airport" && metric) {
      return { ...previous, metric, notes: previous.notes, year }
    }
    if (previous.kind === "compare" && metric) {
      return { ...previous, metric, congestion: congestion || previous.congestion, year }
    }
  }

  const named = found.codes.map((code) => catalog.airports[code]).filter((airport): airport is Airport => Boolean(airport))
  const namedAirport = found.codes.length === 1 && (!places.states || placeIsInsideAirport(places.label, named))

  if (found.codes.length >= 2) {
    return {
      kind: "compare",
      codes: found.codes.slice(0, 4),
      metric: metric ?? (congestion ? "delayRate" : "score"),
      terminal,
      congestion,
      notes: found.notes,
      year,
    }
  }

  if (namedAirport) {
    return {
      kind: "airport",
      code: found.codes[0],
      metric: metric ?? (congestion ? "delayRate" : "score"),
      terminal,
      congestion,
      notes: found.notes,
      year,
    }
  }

  if (compare) {
    return {
      kind: "refuse",
      lookedFor: "two airports to compare",
      reason: "I need two airports I can find in the loaded files. I won't pick the second one.",
    }
  }

  if (rank || places.states) {
    return {
      kind: "rank",
      states: places.states,
      metric: metric ?? "score",
      terminal,
      congestion,
      limit: places.states ? limit : (limit ?? 15),
      groupBy,
      notes: found.notes,
      year,
    }
  }

  return {
    kind: "refuse",
    lookedFor: text,
    reason:
      "I couldn't match that to an airport, a place, or a field I have. I won't guess. Try an airport code, a state, or a metric such as freight, delay, or the capacity score.",
  }
}
