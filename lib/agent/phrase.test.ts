import { afterEach, describe, expect, it } from "vitest"
import { phraseFromRows, resetPhrasePause } from "@/lib/agent/phrase"
import type { QueryResult } from "@/lib/types"

const result: QueryResult = {
  title: "BOS",
  reply: "BOS delay rate was 28.0%.",
  speechText: "BOS delay rate was 28.0%.",
  refusal: false,
  columns: [],
  rows: [{ code: "BOS", delayRate: 0.28 }],
  assumptions: [],
  notes: [],
}

const originalFetch = globalThis.fetch
const originalKey = process.env.OPENAI_API_KEY

afterEach(() => {
  globalThis.fetch = originalFetch
  if (originalKey == null) delete process.env.OPENAI_API_KEY
  else process.env.OPENAI_API_KEY = originalKey
  resetPhrasePause()
})

describe("wording model rate limit", () => {
  it("keeps the filed answer when the model returns 429 and pauses the next call", async () => {
    process.env.OPENAI_API_KEY = "test-key"
    let calls = 0
    globalThis.fetch = async () => {
      calls += 1
      return new Response("busy", { status: 429, headers: { "retry-after": "30" } })
    }
    const first = await phraseFromRows(result)
    expect(first.reply).toBe(result.reply)
    expect(first.modelNote).toMatch(/rate-limited/)
    expect(calls).toBe(1)

    const second = await phraseFromRows(result)
    expect(second.reply).toBe(result.reply)
    expect(second.modelNote).toMatch(/rate-limited/)
    expect(calls).toBe(1)
  })

  it("retries once when the model asks for a short wait", async () => {
    process.env.OPENAI_API_KEY = "test-key"
    let calls = 0
    globalThis.fetch = async () => {
      calls += 1
      if (calls === 1) return new Response("busy", { status: 429, headers: { "retry-after": "0" } })
      return Response.json({ choices: [{ message: { content: "BOS delay rate was 28.0%." } }] })
    }
    const phrased = await phraseFromRows(result)
    expect(calls).toBe(2)
    expect(phrased.reply).toBe("BOS delay rate was 28.0%.")
    expect(phrased.modelNote).toBeNull()
  })

  it("drops a model sentence that adds a number after a successful call", async () => {
    process.env.OPENAI_API_KEY = "test-key"
    globalThis.fetch = async () =>
      Response.json({ choices: [{ message: { content: "About 40% of demand spilled. Delay was 28.0%." } }] })
    const phrased = await phraseFromRows(result)
    expect(phrased.reply).toBe(result.reply)
    expect(phrased.modelNote).toBeNull()
  })
})
