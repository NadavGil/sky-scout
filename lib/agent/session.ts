import type { Intent } from "@/lib/types"

type Store = Map<string, Intent>

const globalStore = globalThis as typeof globalThis & { __skyScoutSessions?: Store }

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
