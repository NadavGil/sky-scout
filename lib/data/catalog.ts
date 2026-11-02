import fs from "fs"
import path from "path"
import { repairCatalog } from "@/lib/data/repair"
import type { Catalog } from "@/lib/types"

let cached: Catalog | null = null

export function getCatalog(): Catalog {
  if (cached) return cached
  const file = path.join(process.cwd(), "data/snapshot/catalog.json")
  cached = repairCatalog(JSON.parse(fs.readFileSync(file, "utf8")) as Catalog)
  return cached
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
