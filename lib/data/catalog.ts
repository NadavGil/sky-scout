import fs from "fs"
import path from "path"
import { fetchLiveCatalog } from "@/lib/data/refresh"
import { repairCatalog } from "@/lib/data/repair"
import type { Catalog } from "@/lib/types"

const DEFAULT_REFRESH_MS = 6 * 60 * 60 * 1000
const MIN_REFRESH_MS = 15 * 60 * 1000
const RETRY_MS = 5 * 60 * 1000

let cached: Catalog | null = null
let live: Catalog | null = null
let refreshing: Promise<Catalog> | null = null
let retryAt = 0
let timer: ReturnType<typeof setTimeout> | null = null

export function getCatalog(): Catalog {
  if (cached) return cached
  const file = path.join(process.cwd(), "data/snapshot/catalog.json")
  cached = repairCatalog(JSON.parse(fs.readFileSync(file, "utf8")) as Catalog)
  return cached
}

function refreshIntervalMs(): number {
  const hours = Number(process.env.SKY_SCOUT_REFRESH_HOURS ?? "6")
  if (!Number.isFinite(hours) || hours <= 0) return DEFAULT_REFRESH_MS
  return Math.max(hours * 60 * 60 * 1000, MIN_REFRESH_MS)
}

function catalogAgeMs(catalog: Catalog): number {
  const builtAt = Date.parse(catalog.builtAt)
  if (Number.isNaN(builtAt)) return Number.POSITIVE_INFINITY
  return Date.now() - builtAt
}

function scheduleRefresh() {
  if (timer) clearTimeout(timer)
  const interval = refreshIntervalMs()
  const wait = live ? Math.max(interval - catalogAgeMs(live), RETRY_MS) : interval
  timer = setTimeout(() => {
    timer = null
    void startRefresh()
  }, wait)
  timer.unref?.()
}

function startRefresh(): Promise<Catalog> {
  if (refreshing) return refreshing
  refreshing = fetchLiveCatalog()
    .then((catalog) => {
      live = repairCatalog(catalog)
      retryAt = 0
      return live
    })
    .catch((error: unknown) => {
      retryAt = Date.now() + RETRY_MS
      console.error("Sky Scout could not refresh the public files.", error)
      return live ?? getCatalog()
    })
    .finally(() => {
      refreshing = null
      scheduleRefresh()
    })
  return refreshing
}

// Serves the catalog already in memory. A catalog older than the refresh
// interval is replaced in the background, and the next request sees the new one.
// The Nov 2 snapshot is used only when no live fetch has succeeded.
export function ensureFreshCatalog(): Promise<Catalog> {
  const current = live
  const stale = !current || catalogAgeMs(current) >= refreshIntervalMs()
  if (!stale) {
    if (!timer) scheduleRefresh()
    return Promise.resolve(current)
  }
  if (Date.now() < retryAt) return Promise.resolve(current ?? getCatalog())
  const pending = startRefresh()
  if (current) return Promise.resolve(current)
  return pending
}

export function catalogSummary(catalog: Catalog) {
  const airports = Object.values(catalog.airports)
  const withScoreInputs = airports.filter((airport) => {
    return (
      airport.longRunways > 0 &&
      airport.ntad?.departures != null &&
      airport.ntad.enplanements != null &&
      airport.ntad.enplanements > 0 &&
      airport.faa?.yoy != null &&
      airport.ontime?.depDelay15Rate != null
    )
  }).length
  return {
    builtAt: catalog.builtAt,
    airportCount: airports.length,
    scoreReadyCount: withScoreInputs,
    unmatchedFaa: catalog.unmatched.faa,
    sources: catalog.sources,
  }
}
