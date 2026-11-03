import { interpret } from "@/lib/agent/parse"
import { phraseFromRows } from "@/lib/agent/phrase"
import { readSession, writeSession } from "@/lib/agent/session"
import { ensureFreshCatalog } from "@/lib/data/catalog"
import { execute } from "@/lib/query/engine"

export const runtime = "nodejs"

export async function POST(request: Request) {
  let body: { message?: unknown; sessionId?: unknown }
  try {
    body = (await request.json()) as { message?: unknown; sessionId?: unknown }
  } catch {
    return Response.json({ error: "Send a JSON body with a message." }, { status: 400 })
  }
  const message = typeof body.message === "string" ? body.message.slice(0, 2000) : ""
  const sessionId = typeof body.sessionId === "string" && body.sessionId ? body.sessionId.slice(0, 80) : "desk"
  const catalog = await ensureFreshCatalog()
  const previous = readSession(sessionId)
  const intent = interpret(message, catalog, previous)
  writeSession(sessionId, intent)
  const result = execute(catalog, intent)
  const reply = await phraseFromRows(result)
  return Response.json({
    sessionId,
    reply,
    speechText: result.speechText,
    refusal: result.refusal,
    title: result.title,
    columns: result.columns,
    rows: result.rows,
    assumptions: result.assumptions,
    notes: result.notes,
  })
}
