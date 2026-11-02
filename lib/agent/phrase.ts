import type { QueryResult } from "@/lib/types"

const NUMBER = /-?\d[\d,]*(?:\.\d+)?%?/g

export function numbersIn(text: string): string[] {
  return (text.match(NUMBER) ?? []).map((value) => value.replace(/,/g, ""))
}

export function modelTextStaysInside(modelText: string, allowedText: string): boolean {
  const allowed = new Set(numbersIn(allowedText))
  return numbersIn(modelText).every((value) => allowed.has(value))
}

export async function phraseFromRows(result: QueryResult): Promise<string> {
  const key = process.env.OPENAI_API_KEY
  if (!key || result.refusal) return result.reply
  const allowed = `${result.reply}\n${JSON.stringify(result.rows)}\n${JSON.stringify(result.assumptions)}`
  try {
    const response = await fetch("https://api.openai.com/v1/chat/completions", {
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
    if (!response.ok) return result.reply
    const payload = (await response.json()) as { choices?: Array<{ message?: { content?: string } }> }
    const text = payload.choices?.[0]?.message?.content?.trim()
    if (!text || !modelTextStaysInside(text, allowed)) return result.reply
    return text
  } catch {
    return result.reply
  }
}
