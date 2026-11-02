import { describe, expect, it } from "vitest"
import { interpret } from "@/lib/agent/parse"
import { getCatalog } from "@/lib/data/catalog"
import { execute } from "@/lib/query/engine"

const catalog = getCatalog()

function ask(message: string) {
  const intent = interpret(message, catalog, null)
  const result = execute(catalog, intent)
  return { intent, result }
}

describe("place and code matching", () => {
  it("keeps South Carolina, West Virginia, and South Bend on those places", () => {
    const carolina = ask("Rank airports in South Carolina")
    expect(carolina.intent.kind).toBe("rank")
    if (carolina.intent.kind !== "rank") return
    expect(carolina.intent.states).toEqual(["SC"])
    expect(carolina.result.rows.every((row) => row.state === "SC")).toBe(true)
    expect(carolina.result.rows.some((row) => row.code === "SSB")).toBe(false)

    const virginia = ask("Rank airports in West Virginia")
    expect(virginia.intent.kind).toBe("rank")
    if (virginia.intent.kind !== "rank") return
    expect(virginia.intent.states).toEqual(["WV"])
    expect(virginia.result.rows.every((row) => row.state === "WV")).toBe(true)

    const bend = ask("Rank airports in South Bend")
    expect(bend.intent.kind).toBe("airport")
    if (bend.intent.kind !== "airport") return
    expect(bend.intent.code).toBe("SBN")
  })

  it("reads MA as Massachusetts and does not rank the whole country", () => {
    const asked = ask("Which airports in MA are strong candidates?")
    expect(asked.intent.kind).toBe("rank")
    if (asked.intent.kind !== "rank") return
    expect(asked.intent.states).toEqual(["MA"])
    expect(asked.result.rows.length).toBeLessThan(30)
    expect(asked.result.rows.every((row) => row.state === "MA")).toBe(true)
  })

  it("does not treat ordinary words as airport codes", () => {
    const far = ask("How far is BOS?")
    expect(far.result.refusal).toBe(true)
    expect(far.result.reply.toLowerCase()).toMatch(/distance/)
    expect(far.result.reply).not.toMatch(/FAR/)

    const sun = ask("What is the sun like at DEN?")
    expect(sun.intent.kind).toBe("airport")
    if (sun.intent.kind !== "airport") return
    expect(sun.intent.code).toBe("DEN")

    const louisiana = ask("Airports in Louisiana")
    expect(louisiana.intent.kind).toBe("rank")
    if (louisiana.intent.kind !== "rank") return
    expect(louisiana.intent.states).toEqual(["LA"])

    const lax = ask("Which airports in LA")
    expect(lax.intent.kind).toBe("airport")
    if (lax.intent.kind !== "airport") return
    expect(lax.intent.code).toBe("LAX")

    const dulles = ask("How delayed is Washington Dulles?")
    expect(dulles.intent.kind).toBe("airport")
    if (dulles.intent.kind !== "airport") return
    expect(dulles.intent.code).toBe("IAD")

    const may = ask("What is the delay rate in May at BOS?")
    expect(may.result.refusal).toBe(true)
    expect(may.result.reply).toMatch(/August 2026/)
  })

  it("answers the named airport when the question also says rank or list", () => {
    const boston = ask("Which airports in Boston are strong candidates?")
    expect(boston.intent.kind).toBe("airport")
    if (boston.intent.kind !== "airport") return
    expect(boston.intent.code).toBe("BOS")

    const freight = ask("list freight at BOS")
    expect(freight.intent.kind).toBe("airport")
    if (freight.intent.kind !== "airport") return
    expect(freight.intent.code).toBe("BOS")
    expect(freight.intent.metric).toBe("freight")
    expect(freight.result.reply).toContain("152,900,687")
  })

  it("asks which Springfield when Boston is already known", () => {
    const asked = ask("Freight at BOS and Springfield")
    expect(asked.intent.kind).toBe("clarify")
    if (asked.intent.kind !== "clarify") return
    expect(asked.intent.options.map((option) => option.code)).toEqual(expect.arrayContaining(["SGF", "SPI"]))
  })

  it("resolves O'Hare and explains the missing Palm Beach join", () => {
    const ohare = ask("passengers at O'Hare")
    expect(ohare.intent.kind).toBe("airport")
    if (ohare.intent.kind !== "airport") return
    expect(ohare.intent.code).toBe("ORD")

    const palm = ask("What is passenger traffic at West Palm Beach?")
    expect(palm.result.refusal).toBe(true)
    expect(palm.result.reply).toMatch(/PBI/)
    expect(palm.result.reply.toLowerCase()).toMatch(/won't substitute/)
  })
})

describe("metrics and years", () => {
  it("treats plural delays as delay, and a why question as delay cause", () => {
    const delays = ask("How are delays at ATL?")
    expect(delays.intent.kind).toBe("airport")
    if (delays.intent.kind !== "airport") return
    expect(delays.intent.metric).toBe("delayRate")
    expect(delays.result.reply.toLowerCase()).toMatch(/delay rate/)
    expect(delays.result.reply.toLowerCase()).not.toMatch(/scores/)

    const causes = ask("Why were delays high at LAX?")
    expect(causes.intent.kind).toBe("airport")
    if (causes.intent.kind !== "airport") return
    expect(causes.intent.metric).toBe("delayCause")
    expect(causes.result.reply.toLowerCase()).toMatch(/carrier/)

    const compared = ask("Compare delay causes at LAX and SNA")
    expect(compared.result.refusal).toBe(false)
    expect(compared.result.columns.map((column) => column.key)).toContain("carrierDelayMinutes")
    const lax = compared.result.rows.find((row) => row.code === "LAX")
    expect(lax?.carrierDelayMinutes).toEqual(expect.any(Number))
  })

  it("does not read May I as the month of May", () => {
    const asked = ask("May I see the delay rate at BOS?")
    expect(asked.result.refusal).toBe(false)
    expect(asked.result.reply).toMatch(/28\.0%/)
  })

  it("refuses a year the field does not have, and returns FAA 2023 when asked", () => {
    const later = ask("How many passengers in 2026 at BOS?")
    expect(later.result.refusal).toBe(true)
    expect(later.result.reply).toMatch(/2026/)
    expect(later.result.reply).not.toMatch(/16,874,194/)

    const earlier = ask("How many enplanements in 2023 at BOS?")
    expect(earlier.result.refusal).toBe(false)
    expect(earlier.result.reply).toContain("19,962,678")
    expect(earlier.result.reply).not.toContain("16,855,500")
  })

  it("shows elevation instead of an empty column", () => {
    const asked = ask("Rank airports by elevation in Rhode Island")
    const pvd = asked.result.rows.find((row) => row.code === "PVD")
    expect(pvd?.elevation).toBe(55)
  })

  it("refuses a top count of zero", () => {
    const asked = ask("top 0 airports in Alaska")
    expect(asked.result.refusal).toBe(true)
  })
})

describe("one national score", () => {
  it("gives PVD the same score in a Rhode Island rank and on its own", () => {
    const alone = ask("What is the capacity score for PVD?")
    const state = ask("Rank airports in Rhode Island")
    const row = state.result.rows.find((item) => item.code === "PVD")
    expect(alone.result.reply).toContain(String(row?.score))
    expect(row?.score).not.toBe(100)
  })

  it("keeps ACK's strong flag the same in New England and on its own", () => {
    const alone = ask("What is the capacity score for ACK?")
    const region = ask("Which airports in New England are strong candidates for terminal expansion?")
    const row = region.result.rows.find((item) => item.code === "ACK")
    expect(row?.score).toBe(alone.result.rows[0]?.score)
    expect(row?.strong).toBe(alone.result.rows[0]?.strong)
  })
})

describe("catalog repair", () => {
  it("drops foreign collisions and merges split FAA ids", () => {
    expect(catalog.airports.A06).toBeUndefined()
    expect(catalog.airports.ME1).toBeUndefined()
    expect(catalog.airports.SAW).toBeUndefined()
    expect(catalog.airports.MQT.faa?.enplanements2024).toBe(44574)
    expect(catalog.airports.MQT.country).toBe("US")
    expect(catalog.aliases?.SAW).toBe("MQT")
    expect(catalog.airports.ROP.faa?.enplanements2024).toBe(10717)
    expect(catalog.airports.SPN.faa?.enplanements2024).toBe(234042)
    expect(catalog.airports.CFK.country).toBe("US")
    expect(catalog.airports.SSB.state).toBe("VI")
    const countries = new Set(Object.values(catalog.airports).map((airport) => airport.country))
    expect([...countries].sort()).toEqual(["AS", "GU", "MP", "PR", "US", "VI"])
  })

  it("answers SAW as Marquette", () => {
    const asked = ask("What is the capacity score for SAW?")
    expect(asked.intent.kind).toBe("airport")
    if (asked.intent.kind !== "airport") return
    expect(asked.intent.code).toBe("MQT")
  })
})
