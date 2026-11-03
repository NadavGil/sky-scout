import type { QueryResult } from "@/lib/types"

const NUMBER = /-?\d[\d,]*(?:\.\d+)?%?/g
const RATE_NOTE = "The wording model was rate-limited, so this is the answer from the loaded files."
const SHORT_RETRY_MS = 1500

let pausedUntil = 0

export function numbersIn(text: string): string[] {
  return (text.match(NUMBER) ?? []).map((value) => value.replace(/,/g, ""))
}

export function modelTextStaysInside(modelText: string, allowedText: string): boolean {
  const allowed = new Set(numbersIn(allowedText))
  return numbersIn(modelText).every((value) => allowed.has(value))
}

export function resetPhrasePause() {
  pausedUntil = 0
}

function retryDelayMs(response: Response): number {
  const header = response.headers.get("retry-after")
  if (!header) return 1000
  const seconds = Number(header)
  if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000)
  const when = Date.parse(header)
  if (Number.isFinite(when)) return Math.max(0, when - Date.now())
  return 1000
}

function pauseFor(ms: number) {
  const wait = Math.min(Math.max(ms, 20_000), 60_000)
  pausedUntil = Date.now() + wait
}

async function requestPhrase(key: string, allowed: string): Promise<Response> {
  return fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${key}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: process.env.OPENAI_MODEL || "gpt-4o-mini",
      temperature: 0,
      messages: [
        {
          role: "system",
          content:
            "Rewrite the analyst reply in plain sentences. Use only numbers that already appear in the reply, rows, or assumptions. Do not add causes, airports, or figures. If you cannot, repeat the reply unchanged.",
        },
        { role: "user", content: allowed },
      ],
    }),
    signal: AbortSignal.timeout(8000),
  })
}

export async function phraseFromRows(result: QueryResult): Promise<{ reply: string; modelNote: string | null }> {
  const key = process.env.OPENAI_API_KEY
  if (!key || result.refusal) return { reply: result.reply, modelNote: null }
  if (Date.now() < pausedUntil) return { reply: result.reply, modelNote: RATE_NOTE }

  const allowed = `${result.reply}\n${JSON.stringify(result.rows)}\n${JSON.stringify(result.assumptions)}`
  try {
    let response = await requestPhrase(key, allowed)
    if (response.status === 429 || response.status === 503) {
      const delay = retryDelayMs(response)
      if (delay <= SHORT_RETRY_MS) {
        await new Promise((resolve) => setTimeout(resolve, delay))
        response = await requestPhrase(key, allowed)
      }
    }
    if (response.status === 429 || response.status === 503) {
      pauseFor(retryDelayMs(response))
      return { reply: result.reply, modelNote: RATE_NOTE }
    }
    if (!response.ok) return { reply: result.reply, modelNote: null }
    const payload = (await response.json()) as { choices?: Array<{ message?: { content?: string } }> }
    const text = payload.choices?.[0]?.message?.content?.trim()
    if (!text || !modelTextStaysInside(text, allowed)) return { reply: result.reply, modelNote: null }
    return { reply: text, modelNote: null }
  } catch {
    return { reply: result.reply, modelNote: null }
  }
}
