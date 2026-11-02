import { describe, expect, it } from "vitest"
import { getCatalog } from "@/lib/data/catalog"
import { interpret } from "@/lib/agent/parse"
import { modelTextStaysInside } from "@/lib/agent/phrase"
import { execute } from "@/lib/query/engine"
import { compositeScore, percentileRanks } from "@/lib/scoring/score"
import type { Airport, Catalog } from "@/lib/types"

function airport(partial: Partial<Airport> & Pick<Airport, "iata" | "state" | "city" | "name">): Airport {
  return {
    country: "US",
    elevationFt: 10,
    lat: 1,
    lon: 1,
    scheduledService: true,
    airportType: "large_airport",
    longRunways: 2,
    ntad: {
      year: 2024,
      enplanements: 1_000_000,
      passengers: 1_000_000,
      departures: 20_000,
      arrivals: 20_000,
      freightLb: 100,
      mail: 5,
    },
    faa: {
      enplanements2024: 1_000_000,
      enplanements2023: 900_000,
      yoy: 1 / 9,
      hub: "S",
      serviceLevel: "P",
    },
    ontime: {
      flights: 100,
      cancelled: 2,
      operated: 98,
      depDelay15Rate: 0.2,
      depDelay15N: 98,
      meanDepDelayMinutes: 12,
      meanTaxiOutMinutes: 15,
      cancellationRate: 0.02,
      carrierDelayMinutes: 10,
      weatherDelayMinutes: 1,
      nasDelayMinutes: 4,
      securityDelayMinutes: 0,
      lateAircraftDelayMinutes: 8,
    },
    ...partial,
  }
}

function catalog(airports: Airport[]): Catalog {
  return {
    builtAt: "2026-11-02T20:40:00Z",
    sources: {
      ourairports: { mode: "test" },
      ntad: { mode: "test", year: 2024 },
      faa: { mode: "test", years: [2023, 2024] },
      ontime: { mode: "test", year: 2026, month: 8 },
    },
    airports: Object.fromEntries(airports.map((item) => [item.iata, item])),
    unmatched: { faa: [], ntad: [] },
  }
}

describe("score math", () => {
  it("ranks percentiles from low to high with ties averaged", () => {
    expect(percentileRanks([1, 2, 3])).toEqual([0, 50, 100])
    expect(percentileRanks([5, 5])).toEqual([50, 50])
  })

  it("does not score an airport missing a runway count", () => {
    const bos = airport({ iata: "BOS", city: "Boston", state: "MA", name: "Logan" })
    const pwm = airport({
      iata: "PWM",
      city: "Portland",
      state: "ME",
      name: "Portland International Jetport",
      longRunways: 0,
    })
    const data = catalog([bos, pwm])
    const intent = interpret("Which airports in New England are strong candidates for terminal expansion?", data, null)
    const result = execute(data, intent)
    const pwmRow = result.rows.find((row) => String(row.airport).includes("PWM"))
    expect(pwmRow?.score).toBeNull()
    expect(String(pwmRow?.missing)).toMatch(/runway/i)
    expect(result.reply.toLowerCase()).toMatch(/terminal/)
    expect(result.reply.toLowerCase()).toMatch(/runway/)
  })

  it("weights the four percentiles without dropping a missing input", () => {
    const score = compositeScore({
      pressurePercentile: 100,
      scalePercentile: 0,
      yoyPercentile: 0,
      delayPercentile: 0,
    })
    expect(score).toBeCloseTo(32)
  })
})

describe("questions the files cannot answer", () => {
  const data = catalog([
    airport({ iata: "ANC", city: "Anchorage", state: "AK", name: "Ted Stevens Anchorage International" }),
    airport({ iata: "SFO", city: "San Francisco", state: "CA", name: "San Francisco International" }),
    airport({ iata: "LAX", city: "Los Angeles", state: "CA", name: "Los Angeles International" }),
    airport({ iata: "SNA", city: "Santa Ana", state: "CA", name: "John Wayne" }),
  ])

  it("refuses a long-haul share and does not invent a percentage", () => {
    const intent = interpret("What is the percentage of long haul flights out of Anchorage airport?", data, null)
    const result = execute(data, intent)
    expect(result.refusal).toBe(true)
    expect(result.rows).toHaveLength(0)
    expect(result.reply).not.toMatch(/\d+(\.\d+)?%/)
  })

  it("refuses unmet demand at SFO", () => {
    const intent = interpret("What is the unmet flight demand in SFO airport and why?", data, null)
    const result = execute(data, intent)
    expect(result.refusal).toBe(true)
    expect(result.reply.toLowerCase()).toMatch(/don't have/)
    expect(result.rows).toHaveLength(0)
  })

  it("compares LAX and SNA congestion from the loaded delay fields", () => {
    const intent = interpret("Compare LA and Santa Ana airport congestion levels.", data, null)
    expect(intent.kind).toBe("compare")
    if (intent.kind !== "compare") return
    expect(intent.codes).toEqual(["LAX", "SNA"])
    const result = execute(data, intent)
    expect(result.refusal).toBe(false)
    expect(result.rows.map((row) => row.code)).toEqual(["LAX", "SNA"])
    expect(result.reply.toLowerCase()).toMatch(/not evidence of spare capacity/)
  })

  it("follows a ranking with a freight question for the same place", () => {
    const first = interpret("Which airports in New England are strong candidates for terminal expansion?", data, null)
    const second = interpret("what about freight", data, first)
    expect(second.kind).toBe("rank")
    if (second.kind !== "rank") return
    expect(second.metric).toBe("freight")
    expect(second.states).toEqual(["CT", "ME", "MA", "NH", "RI", "VT"])
  })
})

describe("loaded snapshot", () => {
  it("returns Anchorage domestic freight from the joined file and nothing else", () => {
    const data = getCatalog()
    expect(data.airports.ANC.ntad?.freightLb).toBe(3116063944)
    const intent = interpret("How much freight moved through Anchorage in 2024?", data, null)
    const result = execute(data, intent)
    expect(result.refusal).toBe(false)
    expect(result.reply).toContain("3,116,063,944")
    expect(result.reply.toLowerCase()).toMatch(/domestic/)
  })
})

describe("model phrasing guard", () => {
  it("drops a model sentence that adds a number", () => {
    expect(modelTextStaysInside("Delay was 21.8%.", "Delay was 21.8%.")).toBe(true)
    expect(modelTextStaysInside("Delay was 21.8%, so about 40% of demand spilled.", "Delay was 21.8%.")).toBe(false)
  })
})
