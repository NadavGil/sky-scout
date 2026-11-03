import { describe, expect, it } from "vitest"
import { buildCatalog, faaRowsFromSheet, foldOnTime, isPavedSurface, parseCsv } from "@/lib/data/refresh"
import { repairCatalog } from "@/lib/data/repair"

describe("public file parsing", () => {
  it("reads quoted csv cells", () => {
    expect(parseCsv('name,city\n"Los Angeles, CA",LA\n')).toEqual([
      ["name", "city"],
      ["Los Angeles, CA", "LA"],
    ])
  })

  it("counts a paved runway of at least 5,000 feet", () => {
    expect(isPavedSurface("ASPH-CONC")).toBe(true)
    expect(isPavedSurface("GRASS")).toBe(false)
    const catalog = buildCatalog({
      builtAt: "2026-11-03T00:00:00.000Z",
      airportsCsv: [
        "ident,type,name,latitude_deg,longitude_deg,elevation_ft,iso_country,iso_region,municipality,scheduled_service,iata_code,local_code",
        "KLAX,large_airport,Los Angeles International,33.94,-118.40,125,US,US-CA,Los Angeles,yes,LAX,LAX",
        "KGRS,small_airport,Grass Strip,0,0,0,US,US-TX,Grass,no,GRS,GRS",
      ].join("\n"),
      runwaysCsv: [
        "airport_ident,length_ft,surface,closed",
        "KLAX,10000,ASP,0",
        "KLAX,8000,CONC,0",
        "KLAX,4000,ASPH,0",
        "KLAX,9000,ASPH,1",
        "KGRS,6000,GRASS,0",
      ].join("\n"),
      ntad: new Map([
        ["LAX", { year: 2024, enplanements: 10, passengers: 10, departures: 4, arrivals: 4, freightLb: 1, mail: 0 }],
        ["GRS", { year: 2024, enplanements: 1, passengers: 1, departures: 1, arrivals: 1, freightLb: 0, mail: 0 }],
      ]),
      faa: new Map([
        ["LAX", { enplanements2024: 20, enplanements2023: 10, yoy: 1, hub: "L", serviceLevel: "P" }],
        ["PBI", { enplanements2024: 1, enplanements2023: 1, yoy: 0, hub: "S", serviceLevel: "P" }],
      ]),
      ontime: new Map(),
    })
    const repaired = repairCatalog(catalog)
    expect(repaired.airports.LAX.longRunways).toBe(2)
    expect(repaired.airports.GRS.longRunways).toBe(0)
    expect(repaired.unmatched.faa).toEqual(["PBI"])
  })

  it("reads FAA enplanements from the header row", () => {
    const rows = faaRowsFromSheet([
      ["U.S. Commercial Service Airports"],
      ["Locid", "CY 24 Enplanements", "CY 23 Enplanements", "Hub", "S/L"],
      ["LAX", "37,760,834", "40,956,673", "L", "P"],
    ])
    const lax = rows.get("LAX")
    expect(lax?.enplanements2024).toBe(37760834)
    expect(lax?.yoy).toBeCloseTo(37760834 / 40956673 - 1)
  })

  it("keeps delay averages on operated flights and skips a blank cause", () => {
    const activity = foldOnTime([
      { cancelled: "0", depDel15: "1", depDelayMinutes: "20", taxiOut: "10", carrierDelay: "5", weatherDelay: "" },
      { cancelled: "1", depDel15: "1", depDelayMinutes: "99", taxiOut: "99", carrierDelay: "", weatherDelay: "" },
    ])
    expect(activity.flights).toBe(2)
    expect(activity.operated).toBe(1)
    expect(activity.cancellationRate).toBe(0.5)
    expect(activity.depDelay15Rate).toBe(1)
    expect(activity.meanDepDelayMinutes).toBe(20)
    expect(activity.meanTaxiOutMinutes).toBe(10)
    expect(activity.carrierDelayMinutes).toBe(5)
    expect(activity.weatherDelayMinutes).toBe(0)
  })
})
