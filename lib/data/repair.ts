import type { Catalog } from "@/lib/types"

const KEEP_COUNTRIES = new Set(["US", "PR", "VI", "GU", "AS", "MP"])

const TERRITORY_STATE: Record<string, string> = {
  PR: "PR",
  VI: "VI",
  GU: "GU",
  AS: "AS",
  MP: "MP",
}

// FAA id -> traffic code for the same airport. The FAA row had been joined to a
// foreign OurAirports record that reused the id.
const MERGE_FAA: Array<{ from: string; into: string }> = [
  { from: "SAW", into: "MQT" },
  { from: "GRO", into: "ROP" },
  { from: "TNI", into: "TIQ" },
  { from: "GSN", into: "SPN" },
]

export function repairCatalog(catalog: Catalog): Catalog {
  const airports = catalog.airports
  const aliases: Record<string, string> = {}
  const unmatchedNtad = [...catalog.unmatched.ntad]

  for (const code of ["CFK", "HLA", "GAL"]) {
    const airport = airports[code]
    if (airport) airport.country = "US"
  }

  for (const pair of MERGE_FAA) {
    const donor = airports[pair.from]
    const target = airports[pair.into]
    if (!donor || !target) continue
    if (!target.faa && donor.faa) target.faa = donor.faa
    if (!target.ontime && donor.ontime) target.ontime = donor.ontime
    delete airports[pair.from]
    aliases[pair.from] = pair.into
  }

  for (const airport of Object.values(airports)) {
    const state = TERRITORY_STATE[airport.country]
    if (state) airport.state = state
  }

  for (const [code, airport] of Object.entries(airports)) {
    if (KEEP_COUNTRIES.has(airport.country)) continue
    if (airport.ntad && !unmatchedNtad.includes(code)) unmatchedNtad.push(code)
    delete airports[code]
  }

  return {
    ...catalog,
    airports,
    aliases,
    unmatched: { faa: [...catalog.unmatched.faa], ntad: unmatchedNtad },
  }
}
