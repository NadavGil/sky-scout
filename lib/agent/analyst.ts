import { modelTextStaysInside } from "@/lib/agent/phrase"
import { execute } from "@/lib/query/engine"
import { REGIONS, STATE_ABBREVIATIONS } from "@/lib/regions"
import type { Catalog, Intent, Metric, QueryResult } from "@/lib/types"

const METRICS = new Set<Metric>([
  "score",
  "enplanements",
  "passengers",
  "departures",
  "arrivals",
  "freight",
  "mail",
  "yoy",
  "delayRate",
  "delayMinutes",
  "taxiOut",
  "cancellationRate",
  "longRunways",
  "delayCause",
  "elevation",
])

const SYSTEM = `You are the analyst for Sky Scout, a desk for US airport capacity. You decide what the question is asking, which airports it refers to, and which of them look promising. A rules parser does not make that judgment.

You do not invent figures. Call a tool, then judge from what it returns. The capacity score is 32% departures per paved runway of at least 5,000 feet, 24% log of 2024 domestic enplanements, 24% FAA enplanement change from 2023 to 2024, and 20% the August 2026 share of departures delayed at least 15 minutes. Percentiles are national. A missing input means no score. A higher delay rate raises the score because the formula treats delay as operational pressure. The strong flag means the national score is at or above the 75th percentile of scored airports with at least 100,000 domestic enplanements in 2024, and FAA enplanements rose. Use that flag as evidence. You still say, in your own words, which airports look promising and why.

The files have domestic 2024 T-100 origin totals, FAA commercial enplanements for 2023 and 2024, August 2026 reporting-carrier on-time results, and OurAirports runways. They do not have seats, gates, load factor, stage length, long-haul share, unmet demand, carriers, routes, international segments, fares, or construction cost. If the question needs one of those, call decline. A question about a terminal still gets the capacity score, and you say you cannot separate a terminal constraint from a runway constraint.

Every number in your reply must already appear in a tool result or in this prompt. Do not add a cause, a cost, or an airport the tools did not return.`

type Turn = { role: "user" | "assistant"; content: string }

type ToolCall = { id: string; function: { name: string; arguments: string } }

type CompletionMessage = {
  content?: string | null
  tool_calls?: ToolCall[]
}

export type ChatCompletion = (body: unknown) => Promise<{ choices?: Array<{ message?: CompletionMessage }> }>

type LookUpArgs = {
  codes?: string[]
  region?: string
  states?: string[]
  metric?: string
  limit?: number
  terminal?: boolean
}

export async function analyzeQuestion(
  message: string,
  catalog: Catalog,
  history: Turn[],
  complete: ChatCompletion = openAiCompletion,
): Promise<QueryResult> {
  const asked = message.trim()
  if (!asked) {
    return refusal("Ask about a field in the loaded files, such as freight, delay, enplanements, or which airports look promising.")
  }
  if (complete === openAiCompletion && !process.env.OPENAI_API_KEY) {
    return refusal(
      "The analyst is a model with tools over the loaded public files. Set OPENAI_API_KEY on the server and restart it. This desk will not answer from the rules parser.",
    )
  }

  const messages: unknown[] = [
    { role: "system", content: SYSTEM },
    ...history.map((turn) => ({ role: turn.role, content: turn.content })),
    { role: "user", content: asked },
  ]
  const allowed: string[] = [SYSTEM, asked]
  let measured: QueryResult | null = null

  for (let step = 0; step < 6; step += 1) {
    let payload: { choices?: Array<{ message?: CompletionMessage }> }
    try {
      payload = await complete({
        model: process.env.OPENAI_MODEL || "gpt-4o-mini",
        temperature: 0,
        messages,
        tools: TOOLS,
      })
    } catch (error) {
      const detail = error instanceof Error ? error.message : "The analyst model did not respond."
      if (measured) return { ...measured, notes: [...measured.notes, detail] }
      return refusal(detail)
    }
    const reply = payload.choices?.[0]?.message
    if (!reply) return measured ?? refusal("The analyst did not return an answer. The loaded files were not changed.")
    const calls = reply.tool_calls ?? []
    if (calls.length === 0) {
      const text = (reply.content ?? "").trim()
      if (!text) return measured ?? refusal("The analyst returned an empty answer.")
      if (!modelTextStaysInside(text, allowed.join("\n"))) {
        if (measured) {
          return {
            ...measured,
            notes: [...measured.notes, "The analyst added a figure that was not in the tool results, so the measured answer was kept."],
          }
        }
        return refusal("The analyst cited a figure that was not in the loaded files, so that answer was discarded.")
      }
      const speechText = text.split(/(?<=\.)\s+/).slice(0, 2).join(" ").slice(0, 500)
      if (measured) return { ...measured, reply: text, speechText }
      return {
        title: "Analyst",
        reply: text,
        speechText,
        refusal: false,
        columns: [],
        rows: [],
        assumptions: [],
        notes: [],
      }
    }

    messages.push({ role: "assistant", content: reply.content ?? "", tool_calls: calls })
    for (const call of calls) {
      if (call.function.name === "decline") {
        const args = parseArgs(call.function.arguments) as { reason?: string }
        return refusal(args.reason?.trim() || "That figure is not in the loaded files.")
      }
      const outcome = runTool(catalog, call.function.name, call.function.arguments)
      allowed.push(outcome.forModel)
      if (outcome.measured) measured = outcome.measured
      messages.push({ role: "tool", tool_call_id: call.id, content: outcome.forModel })
    }
  }

  return measured ?? refusal("The analyst did not finish. Ask a narrower question.")
}

function refusal(reason: string): QueryResult {
  return {
    title: "No answer in the loaded files",
    reply: reason,
    speechText: reason.slice(0, 500),
    refusal: true,
    columns: [],
    rows: [],
    assumptions: [{ label: "What I looked for", detail: "A figure in the loaded public files." }],
    notes: [],
  }
}

function parseArgs(raw: string): Record<string, unknown> {
  try {
    const value = JSON.parse(raw) as unknown
    return value && typeof value === "object" ? (value as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

function runTool(catalog: Catalog, name: string, raw: string): { forModel: string; measured: QueryResult | null } {
  const args = parseArgs(raw)
  if (name === "find_airports") {
    const found = findAirports(catalog, String(args.query ?? ""))
    return { forModel: JSON.stringify(found), measured: null }
  }
  if (name === "look_up") {
    const result = lookUp(catalog, args as LookUpArgs)
    return { forModel: JSON.stringify(slim(result)), measured: result }
  }
  return { forModel: JSON.stringify({ error: `Unknown tool ${name}.` }), measured: null }
}

function findAirports(catalog: Catalog, query: string) {
  const needle = query.trim().toLowerCase()
  if (!needle) return { error: "Provide a name, city, or airport code." }
  const alias = catalog.aliases?.[needle.toUpperCase()]
  const hits = Object.values(catalog.airports)
    .filter((airport) => {
      if (alias && airport.iata === alias) return true
      const hay = `${airport.iata} ${airport.name} ${airport.city} ${airport.state}`.toLowerCase()
      return airport.iata.toLowerCase() === needle || hay.includes(needle)
    })
    .slice(0, 12)
    .map((airport) => ({ code: airport.iata, name: airport.name, city: airport.city, state: airport.state }))
  return { matches: hits, unmatchedFaa: catalog.unmatched.faa }
}

function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []
}

function lookUp(catalog: Catalog, args: LookUpArgs): QueryResult {
  const metric = METRICS.has(args.metric as Metric) ? (args.metric as Metric) : "score"
  const codes = asStrings(args.codes).map((code) => code.trim().toUpperCase()).filter(Boolean)
  const states = resolveStates(args)
  if (states instanceof Error) {
    return refusal(states.message)
  }
  const terminal = Boolean(args.terminal)
  const limit = clampLimit(args.limit)
  let intent: Intent
  if (codes.length === 1) {
    intent = { kind: "airport", code: codes[0], metric, terminal, congestion: metric === "delayRate", notes: [] }
  } else if (codes.length > 1) {
    intent = { kind: "compare", codes, metric, terminal, congestion: metric === "delayRate", notes: [] }
  } else {
    intent = {
      kind: "rank",
      states,
      metric,
      terminal,
      congestion: metric === "delayRate",
      limit,
      groupBy: "airport",
      notes: [],
    }
  }
  return execute(catalog, intent)
}

function resolveStates(args: LookUpArgs): string[] | null | Error {
  if (args.region && args.region.trim()) {
    const key = args.region.trim().toLowerCase()
    const states = REGIONS[key]
    if (!states) return new Error(`Unknown region "${args.region}". Use one of: ${Object.keys(REGIONS).join(", ")}.`)
    return states
  }
  const requested = asStrings(args.states)
  if (!requested.length) return null
  const states = requested.map((state) => state.trim().toUpperCase())
  const unknown = states.filter((state) => !STATE_ABBREVIATIONS.has(state))
  if (unknown.length) return new Error(`Unknown state codes: ${unknown.join(", ")}.`)
  return states
}

function clampLimit(value: number | undefined): number | null {
  if (value == null || !Number.isFinite(value)) return 15
  return Math.min(25, Math.max(1, Math.round(value)))
}

function slim(result: QueryResult) {
  return {
    title: result.title,
    reply: result.reply,
    refusal: result.refusal,
    assumptions: result.assumptions,
    rows: result.rows.slice(0, 25).map((row) => ({
      code: row.code ?? null,
      airport: row.airport ?? null,
      state: row.state ?? null,
      score: row.score ?? null,
      strong: row.strong ?? null,
      yoy: row.yoy ?? null,
      departuresPerRunway: row.departuresPerRunway ?? null,
      delayRate: row.delayRate ?? null,
      domesticEnplanements: row.domesticEnplanements ?? null,
      faaEnplanements2024: row.faaEnplanements2024 ?? null,
      longRunways: row.longRunways ?? null,
      freightLb: row.freightLb ?? null,
      missing: row.missing ?? null,
    })),
  }
}

const TOOLS = [
  {
    type: "function",
    function: {
      name: "find_airports",
      description: "Find airports in the loaded catalog by name, city, or code before judging them.",
      parameters: {
        type: "object",
        properties: { query: { type: "string" } },
        required: ["query"],
        additionalProperties: false,
      },
    },
  },
  {
    type: "function",
    function: {
      name: "look_up",
      description:
        "Read measured figures from the loaded files. Pass codes to inspect or compare airports. Pass a region or state codes to rank a place. Omit both to rank the national set. metric defaults to score.",
      parameters: {
        type: "object",
        properties: {
          codes: { type: "array", items: { type: "string" } },
          region: { type: "string", description: "A census-style region such as new england, pacific, or midwest." },
          states: { type: "array", items: { type: "string" }, description: "USPS state or territory codes." },
          metric: { type: "string" },
          limit: { type: "number" },
          terminal: { type: "boolean", description: "True when the question asks about a terminal or concourse." },
        },
        additionalProperties: false,
      },
    },
  },
  {
    type: "function",
    function: {
      name: "decline",
      description: "Use when the question needs a figure the loaded files do not contain.",
      parameters: {
        type: "object",
        properties: { reason: { type: "string" } },
        required: ["reason"],
        additionalProperties: false,
      },
    },
  },
]

async function openAiCompletion(body: unknown) {
  const response = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${process.env.OPENAI_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(30_000),
  })
  if (!response.ok) throw new Error(`The analyst model returned ${response.status}.`)
  return (await response.json()) as { choices?: Array<{ message?: CompletionMessage }> }
}
