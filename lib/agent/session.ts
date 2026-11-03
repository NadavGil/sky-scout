import type { Intent } from "@/lib/types"

type Store = Map<string, Intent>
type Turn = { role: "user" | "assistant"; content: string }
type Transcripts = Map<string, Turn[]>

const globalStore = globalThis as typeof globalThis & {
  __skyScoutSessions?: Store
  __skyScoutTranscripts?: Transcripts
}

function sessions(): Store {
  if (!globalStore.__skyScoutSessions) globalStore.__skyScoutSessions = new Map()
  return globalStore.__skyScoutSessions
}

export function readSession(id: string): Intent | null {
  return sessions().get(id) ?? null
}

export function writeSession(id: string, intent: Intent) {
  if (intent.kind === "refuse" || intent.kind === "clarify") return
  sessions().set(id, intent)
}

function transcripts(): Transcripts {
  if (!globalStore.__skyScoutTranscripts) globalStore.__skyScoutTranscripts = new Map()
  return globalStore.__skyScoutTranscripts
}

export function readTranscript(id: string): Turn[] {
  return transcripts().get(id) ?? []
}

export function writeTranscript(id: string, turns: Turn[]) {
  transcripts().set(id, turns.slice(-12))
}
