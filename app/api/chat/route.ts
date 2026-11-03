import { analyzeQuestion } from "@/lib/agent/analyst"
import { readTranscript, writeTranscript } from "@/lib/agent/session"
import { ensureFreshCatalog } from "@/lib/data/catalog"

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
  const history = readTranscript(sessionId)
  const result = await analyzeQuestion(message, catalog, history)
  if (!result.refusal) {
    writeTranscript(sessionId, [
      ...history,
      { role: "user", content: message },
      { role: "assistant", content: result.reply },
    ])
  }
  return Response.json({
    sessionId,
    reply: result.reply,
    speechText: result.speechText,
    refusal: result.refusal,
    title: result.title,
    columns: result.columns,
    rows: result.rows,
    assumptions: result.assumptions,
    notes: result.notes,
  })
}
