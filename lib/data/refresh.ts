import { spawn } from "node:child_process"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import type { Airport, Catalog, FaaActivity, NtadActivity, OnTimeActivity } from "@/lib/types"

const OURAIRPORTS_AIRPORTS = "https://davidmegginson.github.io/ourairports-data/airports.csv"
const OURAIRPORTS_RUNWAYS = "https://davidmegginson.github.io/ourairports-data/runways.csv"
const NTAD_QUERY =
  "https://services.arcgis.com/xOi1kZaI0eWDREZv/ArcGIS/rest/services/T100_Domestic_Market_and_Segment_Data/FeatureServer/1/query"
const FAA_XLSX = "https://www.faa.gov/sites/faa.gov/files/2026-09/arp-cy2024-commercial-service-enplanements.xlsx"
const ONTIME_ZIP =
  "https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2026_8.zip"

const PAVED = new Set(["ASP", "ASPH", "ASPHALT", "BIT", "BITUMEN", "CON", "CONC", "CONCRETE", "COP", "PAVED", "PEM", "TAR"])

type AirportDraft = {
  ident: string
  iata: string
  local: string
  name: string
  city: string
  state: string
  country: string
  elevationFt: number | null
  lat: number | null
  lon: number | null
  scheduledService: boolean
  airportType: string
  longRunways: number
  ntad: NtadActivity | null
  faa: FaaActivity | null
  ontime: OnTimeActivity | null
}

export function isPavedSurface(surface: string): boolean {
  const tokens = surface.toUpperCase().split(/[^A-Z0-9]+/).filter(Boolean)
  return tokens.some((token) => PAVED.has(token))
}

export function parseCsv(text: string): string[][] {
  const rows: string[][] = []
  feedCsv(text, (row) => rows.push(row))
  return rows
}

export function feedCsv(text: string, onRow: (row: string[]) => void): void {
  let row: string[] = []
  let cell = ""
  let quoted = false
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index]
    if (quoted) {
      if (char === '"') {
        if (text[index + 1] === '"') {
          cell += '"'
          index += 1
        } else quoted = false
      } else cell += char
      continue
    }
    if (char === '"') {
      quoted = true
    } else if (char === ",") {
      row.push(cell)
      cell = ""
    } else if (char === "\n") {
      row.push(cell)
      onRow(cleanRow(row))
      row = []
      cell = ""
    } else if (char !== "\r") {
      cell += char
    }
  }
  if (cell.length > 0 || row.length > 0) {
    row.push(cell)
    onRow(cleanRow(row))
  }
}

function cleanRow(row: string[]): string[] {
  if (row.length > 0) row[0] = row[0].replace(/^\uFEFF/, "")
  return row
}

function columnIndex(header: string[], name: string): number {
  const wanted = name.toLowerCase()
  return header.findIndex((cell) => cell.trim().toLowerCase() === wanted)
}

function numberOrNull(value: string | undefined): number | null {
  if (value == null) return null
  const trimmed = value.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed.replace(/,/g, ""))
  return Number.isFinite(parsed) ? parsed : null
}

function stateCode(country: string, region: string): string {
  const prefix = `${country}-`
  if (region.startsWith(prefix)) return region.slice(prefix.length)
  if (region.startsWith("US-")) return region.slice(3)
  return region
}

export function foldOnTime(flights: Array<Record<string, string>>): OnTimeActivity {
  let total = 0
  let cancelled = 0
  let delayed = 0
  let delayMinutes = 0
  let delayCount = 0
  let taxiMinutes = 0
  let taxiCount = 0
  let carrier = 0
  let weather = 0
  let nas = 0
  let security = 0
  let lateAircraft = 0
  for (const flight of flights) {
    total += 1
    const wasCancelled = numberOrNull(flight.cancelled) === 1
    if (wasCancelled) cancelled += 1
    if (!wasCancelled && numberOrNull(flight.depDel15) === 1) delayed += 1
    if (!wasCancelled) {
      const delay = numberOrNull(flight.depDelayMinutes)
      if (delay != null) {
        delayMinutes += delay
        delayCount += 1
      }
      const taxi = numberOrNull(flight.taxiOut)
      if (taxi != null) {
        taxiMinutes += taxi
        taxiCount += 1
      }
    }
    carrier += numberOrNull(flight.carrierDelay) ?? 0
    weather += numberOrNull(flight.weatherDelay) ?? 0
    nas += numberOrNull(flight.nasDelay) ?? 0
    security += numberOrNull(flight.securityDelay) ?? 0
    lateAircraft += numberOrNull(flight.lateAircraftDelay) ?? 0
  }
  const operated = total - cancelled
  return {
    flights: total,
    cancelled,
    operated,
    depDelay15Rate: operated > 0 ? delayed / operated : null,
    depDelay15N: operated,
    meanDepDelayMinutes: delayCount > 0 ? delayMinutes / delayCount : null,
    meanTaxiOutMinutes: taxiCount > 0 ? taxiMinutes / taxiCount : null,
    cancellationRate: total > 0 ? cancelled / total : null,
    carrierDelayMinutes: carrier,
    weatherDelayMinutes: weather,
    nasDelayMinutes: nas,
    securityDelayMinutes: security,
    lateAircraftDelayMinutes: lateAircraft,
  }
}

type OnTimeBucket = {
  flights: number
  cancelled: number
  delayed: number
  delayMinutes: number
  delayCount: number
  taxiMinutes: number
  taxiCount: number
  carrier: number
  weather: number
  nas: number
  security: number
  lateAircraft: number
}

function emptyBucket(): OnTimeBucket {
  return {
    flights: 0,
    cancelled: 0,
    delayed: 0,
    delayMinutes: 0,
    delayCount: 0,
    taxiMinutes: 0,
    taxiCount: 0,
    carrier: 0,
    weather: 0,
    nas: 0,
    security: 0,
    lateAircraft: 0,
  }
}

function bucketToActivity(bucket: OnTimeBucket): OnTimeActivity {
  const operated = bucket.flights - bucket.cancelled
  return {
    flights: bucket.flights,
    cancelled: bucket.cancelled,
    operated,
    depDelay15Rate: operated > 0 ? bucket.delayed / operated : null,
    depDelay15N: operated,
    meanDepDelayMinutes: bucket.delayCount > 0 ? bucket.delayMinutes / bucket.delayCount : null,
    meanTaxiOutMinutes: bucket.taxiCount > 0 ? bucket.taxiMinutes / bucket.taxiCount : null,
    cancellationRate: bucket.flights > 0 ? bucket.cancelled / bucket.flights : null,
    carrierDelayMinutes: bucket.carrier,
    weatherDelayMinutes: bucket.weather,
    nasDelayMinutes: bucket.nas,
    securityDelayMinutes: bucket.security,
    lateAircraftDelayMinutes: bucket.lateAircraft,
  }
}

function preferDraft(current: AirportDraft, incoming: AirportDraft, code: string): AirportDraft {
  const rank = (draft: AirportDraft) => {
    let score = 0
    if (draft.iata === code) score += 8
    if (draft.country === "US" || draft.country === "PR" || draft.country === "VI" || draft.country === "GU" || draft.country === "AS" || draft.country === "MP") score += 4
    if (draft.scheduledService) score += 2
    if (draft.airportType === "large_airport") score += 1
    return score
  }
  return rank(incoming) > rank(current) ? incoming : current
}

export function buildCatalog(input: {
  airportsCsv: string
  runwaysCsv: string
  ntad: Map<string, NtadActivity>
  faa: Map<string, FaaActivity>
  ontime: Map<string, OnTimeActivity>
  builtAt: string
}): Catalog {
  const airportRows = parseCsv(input.airportsCsv)
  const runwayRows = parseCsv(input.runwaysCsv)
  const airportHeader = airportRows[0] ?? []
  const runwayHeader = runwayRows[0] ?? []
  const airportCol = (name: string) => {
    const index = columnIndex(airportHeader, name)
    if (index < 0) throw new Error(`OurAirports airports file has no ${name} column.`)
    return index
  }
  const runwayCol = (name: string) => {
    const index = columnIndex(runwayHeader, name)
    if (index < 0) throw new Error(`OurAirports runways file has no ${name} column.`)
    return index
  }
  const identCol = airportCol("ident")
  const typeCol = airportCol("type")
  const nameCol = airportCol("name")
  const latCol = airportCol("latitude_deg")
  const lonCol = airportCol("longitude_deg")
  const elevationCol = airportCol("elevation_ft")
  const countryCol = airportCol("iso_country")
  const regionCol = airportCol("iso_region")
  const cityCol = airportCol("municipality")
  const scheduledCol = airportCol("scheduled_service")
  const iataCol = airportCol("iata_code")
  const localCol = airportCol("local_code")
  const runwayIdent = runwayCol("airport_ident")
  const lengthCol = runwayCol("length_ft")
  const surfaceCol = runwayCol("surface")
  const closedCol = runwayCol("closed")

  const longRunways = new Map<string, number>()
  for (const row of runwayRows.slice(1)) {
    const ident = (row[runwayIdent] ?? "").trim().toUpperCase()
    if (!ident) continue
    const closed = (row[closedCol] ?? "").trim()
    if (closed === "1" || closed.toLowerCase() === "yes") continue
    const length = numberOrNull(row[lengthCol])
    if (length == null || length < 5000) continue
    if (!isPavedSurface(row[surfaceCol] ?? "")) continue
    longRunways.set(ident, (longRunways.get(ident) ?? 0) + 1)
  }

  const byCode = new Map<string, AirportDraft>()
  for (const row of airportRows.slice(1)) {
    if ((row[typeCol] ?? "").trim() === "closed") continue
    const iata = (row[iataCol] ?? "").trim().toUpperCase()
    const local = (row[localCol] ?? "").trim().toUpperCase()
    if (!iata && !local) continue
    const ident = (row[identCol] ?? "").trim().toUpperCase()
    const country = (row[countryCol] ?? "").trim().toUpperCase()
    const draft: AirportDraft = {
      ident,
      iata,
      local,
      name: (row[nameCol] ?? "").trim(),
      city: (row[cityCol] ?? "").trim(),
      state: stateCode(country, (row[regionCol] ?? "").trim().toUpperCase()),
      country,
      elevationFt: numberOrNull(row[elevationCol]),
      lat: numberOrNull(row[latCol]),
      lon: numberOrNull(row[lonCol]),
      scheduledService: (row[scheduledCol] ?? "").trim().toLowerCase() === "yes",
      airportType: (row[typeCol] ?? "").trim(),
      longRunways: longRunways.get(ident) ?? 0,
      ntad: null,
      faa: null,
      ontime: null,
    }
    for (const code of new Set([iata, local].filter(Boolean))) {
      const current = byCode.get(code)
      byCode.set(code, current ? preferDraft(current, draft, code) : draft)
    }
  }

  const unmatchedNtad: string[] = []
  for (const [code, activity] of input.ntad) {
    const airport = byCode.get(code)
    if (!airport) {
      unmatchedNtad.push(code)
      continue
    }
    airport.ntad = activity
  }
  const unmatchedFaa: string[] = []
  for (const [code, activity] of input.faa) {
    const airport = byCode.get(code)
    if (!airport) {
      unmatchedFaa.push(code)
      continue
    }
    airport.faa = activity
  }
  for (const [code, activity] of input.ontime) {
    const airport = byCode.get(code)
    if (airport) airport.ontime = activity
  }

  const airports: Record<string, Airport> = {}
  for (const draft of new Set(byCode.values())) {
    if (!draft.ntad && !draft.faa && !draft.ontime) continue
    const key = draft.iata || draft.local
    if (!key || airports[key]) continue
    airports[key] = {
      iata: key,
      name: draft.name,
      city: draft.city,
      state: draft.state,
      country: draft.country,
      elevationFt: draft.elevationFt,
      lat: draft.lat,
      lon: draft.lon,
      scheduledService: draft.scheduledService,
      airportType: draft.airportType,
      longRunways: draft.longRunways,
      ntad: draft.ntad,
      faa: draft.faa,
      ontime: draft.ontime,
    }
  }

  return {
    builtAt: input.builtAt,
    sources: {
      ourairports: {
        airports: OURAIRPORTS_AIRPORTS,
        runways: OURAIRPORTS_RUNWAYS,
        mode: "live",
      },
      ntad: {
        url: NTAD_QUERY,
        year: 2024,
        origins: input.ntad.size,
        mode: "live",
        note: "2024 domestic T-100 origin totals for US carriers. No seats, distance, carrier, or month.",
      },
      faa: {
        url: FAA_XLSX,
        years: [2023, 2024],
        rows: input.faa.size,
        mode: "live",
        note: "Commercial service enplanements, calendar years 2024 and 2023.",
      },
      ontime: {
        url: ONTIME_ZIP,
        year: 2026,
        month: 8,
        mode: "live",
        note: "Domestic reporting-carrier flights, August 2026, fetched from the public monthly zip and reduced to the origin airport. Delay-cause minutes are those reported on the flight record.",
      },
    },
    airports,
    unmatched: {
      faa: unmatchedFaa.sort(),
      ntad: unmatchedNtad.sort(),
    },
  }
}

function curl(args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn("curl", ["-fsSL", "--retry", "2", "-A", "Mozilla/5.0 (compatible; SkyScout/0.1)", ...args], {
      stdio: ["ignore", "pipe", "pipe"],
    })
    let errorText = ""
    child.stdout.resume()
    child.stderr.on("data", (chunk: Buffer) => {
      errorText += chunk.toString()
    })
    child.on("error", reject)
    child.on("close", (code) => {
      if (code !== 0) reject(new Error(errorText.trim() || `curl exited ${code}`))
      else resolve()
    })
  })
}

async function fetchText(url: string): Promise<string> {
  const chunks: Buffer[] = []
  await new Promise<void>((resolve, reject) => {
    const child = spawn("curl", ["-fsSL", "--retry", "2", "--max-time", "120", "-A", "Mozilla/5.0 (compatible; SkyScout/0.1)", url], {
      stdio: ["ignore", "pipe", "pipe"],
    })
    let errorText = ""
    child.stdout.on("data", (chunk: Buffer) => chunks.push(chunk))
    child.stderr.on("data", (chunk: Buffer) => {
      errorText += chunk.toString()
    })
    child.on("error", reject)
    child.on("close", (code) => {
      if (code !== 0) reject(new Error(errorText.trim() || `curl exited ${code}`))
      else resolve()
    })
  })
  return Buffer.concat(chunks).toString("utf8")
}

async function downloadTo(url: string, destination: string): Promise<void> {
  await curl(["--max-time", "600", "-o", destination, url])
}

async function fetchNtad(): Promise<Map<string, NtadActivity>> {
  const map = new Map<string, NtadActivity>()
  let offset = 0
  for (;;) {
    const url = new URL(NTAD_QUERY)
    url.searchParams.set("where", "year=2024")
    url.searchParams.set("outFields", "year,origin,enplanements,passengers,departures,arrivals,freight,mail")
    url.searchParams.set("returnGeometry", "false")
    url.searchParams.set("f", "json")
    url.searchParams.set("resultOffset", String(offset))
    url.searchParams.set("resultRecordCount", "2000")
    const body = JSON.parse(await fetchText(url.toString())) as {
      error?: { message?: string }
      exceededTransferLimit?: boolean
      features?: Array<{ attributes: Record<string, number | string | null> }>
    }
    if (body.error) throw new Error(body.error.message ?? "NTAD query failed")
    const features = body.features ?? []
    for (const feature of features) {
      const attributes = feature.attributes
      const code = String(attributes.origin ?? "").trim().toUpperCase()
      if (!code) continue
      map.set(code, {
        year: numberOrNull(String(attributes.year ?? "")),
        enplanements: numberOrNull(String(attributes.enplanements ?? "")),
        passengers: numberOrNull(String(attributes.passengers ?? "")),
        departures: numberOrNull(String(attributes.departures ?? "")),
        arrivals: numberOrNull(String(attributes.arrivals ?? "")),
        freightLb: numberOrNull(String(attributes.freight ?? "")),
        mail: numberOrNull(String(attributes.mail ?? "")),
      })
    }
    if (!body.exceededTransferLimit || features.length === 0) break
    offset += features.length
  }
  if (map.size === 0) throw new Error("NTAD returned no 2024 origin rows.")
  return map
}

function decodeXml(value: string): string {
  return value
    .replace(/&#(\d+);/g, (_, code: string) => String.fromCodePoint(Number(code)))
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
}

function sharedStrings(xml: string): string[] {
  const strings: string[] = []
  for (const item of xml.matchAll(/<si\b[^>]*>([\s\S]*?)<\/si>/g)) {
    const text = [...item[1].matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)].map((match) => decodeXml(match[1])).join("")
    strings.push(text)
  }
  return strings
}

function sheetRows(xml: string, strings: string[]): string[][] {
  const rows: string[][] = []
  for (const row of xml.matchAll(/<row\b[^>]*>([\s\S]*?)<\/row>/g)) {
    const cells: string[] = []
    for (const cell of row[1].matchAll(/<c\b([^>]*)>([\s\S]*?)<\/c>/g)) {
      const ref = /r="([A-Z]+)\d+"/i.exec(cell[1])
      const type = /t="([^"]+)"/.exec(cell[1])?.[1]
      let column = cells.length
      if (ref) {
        column = 0
        for (const letter of ref[1]) column = column * 26 + (letter.charCodeAt(0) - 64)
        column -= 1
      }
      const raw = /<v>([\s\S]*?)<\/v>/.exec(cell[2])?.[1] ?? /<t[^>]*>([\s\S]*?)<\/t>/.exec(cell[2])?.[1] ?? ""
      const value = type === "s" ? strings[Number(raw)] ?? "" : decodeXml(raw)
      while (cells.length < column) cells.push("")
      cells[column] = value
    }
    rows.push(cells)
  }
  return rows
}

function headerIndex(header: string[], pattern: RegExp): number {
  return header.findIndex((cell) => pattern.test(cell.trim().toLowerCase().replace(/[^a-z0-9]+/g, "")))
}

export function faaRowsFromSheet(rows: string[][]): Map<string, FaaActivity> {
  let headerAt = -1
  for (let index = 0; index < Math.min(rows.length, 30); index += 1) {
    if (headerIndex(rows[index], /^locid$/) >= 0) {
      headerAt = index
      break
    }
  }
  if (headerAt < 0) throw new Error("FAA workbook has no Locid column.")
  const header = rows[headerAt]
  const locid = headerIndex(header, /^locid$/)
  const year2024 = headerIndex(header, /^(cy24|cy2024|2024).*enplane/)
  const year2023 = headerIndex(header, /^(cy23|cy2023|2023).*enplane/)
  const hub = headerIndex(header, /^hub$/)
  const service = headerIndex(header, /^(sl|servicelevel)$/)
  if (year2024 < 0 || year2023 < 0) throw new Error("FAA workbook has no 2023 and 2024 enplanement columns.")
  const map = new Map<string, FaaActivity>()
  for (const row of rows.slice(headerAt + 1)) {
    const code = (row[locid] ?? "").trim().toUpperCase()
    if (!/^[A-Z0-9]{3,4}$/.test(code)) continue
    const enplanements2024 = numberOrNull(row[year2024])
    const enplanements2023 = numberOrNull(row[year2023])
    map.set(code, {
      enplanements2024,
      enplanements2023,
      yoy: enplanements2024 != null && enplanements2023 != null && enplanements2023 !== 0 ? enplanements2024 / enplanements2023 - 1 : null,
      hub: hub >= 0 ? (row[hub] ?? "").trim() || null : null,
      serviceLevel: service >= 0 ? (row[service] ?? "").trim() || null : null,
    })
  }
  if (map.size === 0) throw new Error("FAA workbook has no airport rows.")
  return map
}

function unzipText(zipPath: string, entry: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn("unzip", ["-p", zipPath, entry], { stdio: ["ignore", "pipe", "pipe"] })
    const chunks: Buffer[] = []
    let errorText = ""
    child.stdout.on("data", (chunk: Buffer) => chunks.push(chunk))
    child.stderr.on("data", (chunk: Buffer) => {
      errorText += chunk.toString()
    })
    child.on("error", reject)
    child.on("close", (code) => {
      if (code !== 0) reject(new Error(errorText || `unzip exited ${code}`))
      else resolve(Buffer.concat(chunks).toString("utf8"))
    })
  })
}

function unzipList(zipPath: string): Promise<Array<{ name: string; length: number }>> {
  return new Promise((resolve, reject) => {
    const child = spawn("unzip", ["-l", zipPath], { stdio: ["ignore", "pipe", "pipe"] })
    let output = ""
    child.stdout.on("data", (chunk: Buffer) => {
      output += chunk.toString()
    })
    child.on("error", reject)
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`Could not list ${zipPath}`))
        return
      }
      const entries: Array<{ name: string; length: number }> = []
      for (const line of output.split("\n")) {
        const match = /^\s*(\d+)\s+\d{2}-\d{2}-\d{2,4}\s+\d{2}:\d{2}\s+(.+)$/.exec(line)
        if (match) entries.push({ length: Number(match[1]), name: match[2].trim() })
      }
      resolve(entries)
    })
  })
}

async function fetchFaa(directory: string): Promise<Map<string, FaaActivity>> {
  const file = path.join(directory, "faa.xlsx")
  await downloadTo(FAA_XLSX, file)
  return readFaaWorkbook(file)
}

export async function readFaaWorkbook(file: string): Promise<Map<string, FaaActivity>> {
  const entries = await unzipList(file)
  const shared = entries.find((entry) => entry.name === "xl/sharedStrings.xml")
  const strings = shared ? sharedStrings(await unzipText(file, shared.name)) : []
  const sheets = entries.filter((entry) => /^xl\/worksheets\/sheet\d+\.xml$/.test(entry.name))
  if (sheets.length === 0) throw new Error("FAA workbook has no worksheet.")
  let lastError: Error | null = null
  for (const sheet of sheets) {
    try {
      return faaRowsFromSheet(sheetRows(await unzipText(file, sheet.name), strings))
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error))
    }
  }
  throw lastError ?? new Error("FAA workbook could not be read.")
}

async function fetchOnTime(directory: string): Promise<Map<string, OnTimeActivity>> {
  const file = path.join(directory, "ontime.zip")
  await downloadTo(ONTIME_ZIP, file)
  return readOnTimeZip(file)
}

export async function readOnTimeZip(file: string): Promise<Map<string, OnTimeActivity>> {
  const entries = await unzipList(file)
  const csv = entries
    .filter((entry) => entry.name.toLowerCase().endsWith(".csv"))
    .sort((a, b) => b.length - a.length)[0]
  if (!csv) throw new Error("On-time zip has no CSV.")
  const buckets = new Map<string, OnTimeBucket>()
  await new Promise<void>((resolve, reject) => {
    const child = spawn("unzip", ["-p", file, csv.name], { stdio: ["ignore", "pipe", "pipe"] })
    let failed = false
    let header: string[] | null = null
    let origin = -1
    let cancelled = -1
    let delayed = -1
    let delayMinutes = -1
    let taxi = -1
    let carrier = -1
    let weather = -1
    let nas = -1
    let security = -1
    let lateAircraft = -1
    let row: string[] = []
    let cell = ""
    let quoted = false
    let pendingQuote = false
    const fail = (error: Error) => {
      if (failed) return
      failed = true
      child.kill()
      reject(error)
    }
    const take = (record: string[]) => {
      if (!header) {
        header = record
        origin = columnIndex(header, "origin")
        cancelled = columnIndex(header, "cancelled")
        delayed = columnIndex(header, "depdel15")
        delayMinutes = columnIndex(header, "depdelayminutes")
        taxi = columnIndex(header, "taxiout")
        carrier = columnIndex(header, "carrierdelay")
        weather = columnIndex(header, "weatherdelay")
        nas = columnIndex(header, "nasdelay")
        security = columnIndex(header, "securitydelay")
        lateAircraft = columnIndex(header, "lateaircraftdelay")
        if (origin < 0 || cancelled < 0 || delayed < 0) fail(new Error("On-time CSV is missing Origin, Cancelled, or DepDel15."))
        return
      }
      const code = (record[origin] ?? "").trim().toUpperCase()
      if (!code) return
      const bucket = buckets.get(code) ?? emptyBucket()
      const wasCancelled = numberOrNull(record[cancelled]) === 1
      bucket.flights += 1
      if (wasCancelled) bucket.cancelled += 1
      if (!wasCancelled && numberOrNull(record[delayed]) === 1) bucket.delayed += 1
      if (!wasCancelled) {
        const delay = delayMinutes >= 0 ? numberOrNull(record[delayMinutes]) : null
        if (delay != null) {
          bucket.delayMinutes += delay
          bucket.delayCount += 1
        }
        const taxiOut = taxi >= 0 ? numberOrNull(record[taxi]) : null
        if (taxiOut != null) {
          bucket.taxiMinutes += taxiOut
          bucket.taxiCount += 1
        }
      }
      if (carrier >= 0) bucket.carrier += numberOrNull(record[carrier]) ?? 0
      if (weather >= 0) bucket.weather += numberOrNull(record[weather]) ?? 0
      if (nas >= 0) bucket.nas += numberOrNull(record[nas]) ?? 0
      if (security >= 0) bucket.security += numberOrNull(record[security]) ?? 0
      if (lateAircraft >= 0) bucket.lateAircraft += numberOrNull(record[lateAircraft]) ?? 0
      buckets.set(code, bucket)
    }
    const push = (chunk: string) => {
      let index = 0
      if (pendingQuote) {
        pendingQuote = false
        if (chunk.startsWith('"')) {
          cell += '"'
          index = 1
        } else quoted = false
      }
      for (; index < chunk.length; index += 1) {
        const char = chunk[index]
        if (quoted) {
          if (char === '"') {
            if (index + 1 >= chunk.length) {
              pendingQuote = true
              continue
            }
            if (chunk[index + 1] === '"') {
              cell += '"'
              index += 1
            } else quoted = false
          } else cell += char
          continue
        }
        if (char === '"') quoted = true
        else if (char === ",") {
          row.push(cell)
          cell = ""
        } else if (char === "\n") {
          row.push(cell)
          take(cleanRow(row))
          row = []
          cell = ""
        } else if (char !== "\r") cell += char
      }
    }
    child.stdout.on("data", (chunk: Buffer) => {
      if (!failed) push(chunk.toString("utf8"))
    })
    child.on("error", (error) => fail(error))
    child.on("close", (code) => {
      if (failed) return
      if (cell.length > 0 || row.length > 0) push("\n")
      if (code !== 0) fail(new Error(`unzip exited ${code}`))
      else resolve()
    })
  })
  if (buckets.size === 0) throw new Error("On-time file produced no origin totals.")
  const map = new Map<string, OnTimeActivity>()
  for (const [code, bucket] of buckets) map.set(code, bucketToActivity(bucket))
  return map
}

export async function fetchLiveCatalog(): Promise<Catalog> {
  const directory = await mkdtemp(path.join(tmpdir(), "sky-scout-"))
  try {
    const [airportsCsv, runwaysCsv, ntad, faa, ontime] = await Promise.all([
      fetchText(OURAIRPORTS_AIRPORTS),
      fetchText(OURAIRPORTS_RUNWAYS),
      fetchNtad(),
      fetchFaa(directory),
      fetchOnTime(directory),
    ])
    return buildCatalog({
      airportsCsv,
      runwaysCsv,
      ntad,
      faa,
      ontime,
      builtAt: new Date().toISOString(),
    })
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
}
