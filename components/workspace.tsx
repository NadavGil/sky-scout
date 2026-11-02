"use client"

import { useEffect, useRef, useState } from "react"
import { Mic, Square, Volume2, VolumeX } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

type Assumption = { label: string; detail: string }
type Column = { key: string; label: string }
type Row = Record<string, string | number | boolean | null>

type Answer = {
  reply: string
  speechText: string
  refusal: boolean
  title: string
  columns: Column[]
  rows: Row[]
  assumptions: Assumption[]
  notes: string[]
}

type ChatMessage = { id: string; role: "user" | "assistant"; text: string; answer?: Answer }

type Summary = {
  builtAt: string
  airportCount: number
  scoreReadyCount: number
  unmatchedFaa: string[]
  sources: {
    ntad: { year?: number; note?: string }
    faa: { years?: number[]; note?: string }
    ontime: { year?: number; month?: number; note?: string }
  }
}

const PROMPTS = [
  "Which airports in New England are strong candidates for terminal expansion?",
  "Compare LA and Santa Ana airport congestion levels.",
  "What is the percentage of long haul flights out of Anchorage airport?",
  "What is the unmet flight demand in SFO airport and why?",
  "How much freight moved through Anchorage in 2024?",
]

const RATIO_KEYS = new Set(["yoy", "delayRate", "cancellationRate"])

function formatCell(key: string, value: Row[string]): string {
  if (value == null || value === "") return "Not in file"
  if (typeof value === "boolean") return value ? "Yes" : "No"
  if (typeof value === "number") {
    if (RATIO_KEYS.has(key)) return `${(value * 100).toFixed(1)}%`
    if (key === "meanDelayMinutes" || key === "taxiOut") {
      return value.toLocaleString("en-US", { maximumFractionDigits: 1 })
    }
    return value.toLocaleString("en-US", { maximumFractionDigits: 1 })
  }
  return String(value)
}

function builtLabel(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Jerusalem",
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date) + " Asia/Jerusalem"
}

export function Workspace({ summary }: { summary: Summary }) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [speak, setSpeak] = useState(false)
  const [listening, setListening] = useState(false)
  const [voiceNote, setVoiceNote] = useState<string | null>(null)
  const [panel, setPanel] = useState<"chat" | "result">("chat")
  const sessionId = useRef(typeof crypto !== "undefined" ? crypto.randomUUID() : "desk")
  const recognition = useRef<SpeechRecognition | null>(null)
  const bottom = useRef<HTMLDivElement | null>(null)
  const latest = messages.findLast((message) => message.answer)

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" })
  }, [messages, loading])

  useEffect(() => {
    if (!speak || !latest?.answer) return
    if (typeof window === "undefined" || !window.speechSynthesis) return
    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(latest.answer.speechText)
    window.speechSynthesis.speak(utterance)
  }, [speak, latest])

  async function ask(text: string) {
    const message = text.trim()
    if (!message || loading) return
    setDraft("")
    setError(null)
    setPanel("chat")
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: "user", text: message }])
    setLoading(true)
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, sessionId: sessionId.current }),
      })
      if (!response.ok) throw new Error("The desk could not read that request.")
      const answer = (await response.json()) as Answer
      setMessages((current) => [
        ...current,
        { id: crypto.randomUUID(), role: "assistant", text: answer.reply, answer },
      ])
      setPanel("result")
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The desk could not read that request.")
    } finally {
      setLoading(false)
    }
  }

  function toggleMic() {
    const SpeechApi = window.SpeechRecognition ?? window.webkitSpeechRecognition
    if (!SpeechApi) {
      setVoiceNote("This browser has no speech recognition. Type the question instead.")
      return
    }
    if (listening && recognition.current) {
      recognition.current.stop()
      setListening(false)
      return
    }
    const engine = new SpeechApi()
    engine.lang = "en-US"
    engine.interimResults = false
    engine.onresult = (event) => {
      const said = event.results[0]?.[0]?.transcript ?? ""
      if (said) void ask(said)
    }
    engine.onerror = () => {
      setVoiceNote("The microphone did not return any speech.")
      setListening(false)
    }
    engine.onend = () => setListening(false)
    recognition.current = engine
    setVoiceNote(null)
    setListening(true)
    engine.start()
  }

  const answer = latest?.answer

  return (
    <div className="min-h-screen">
      <header className="border-b border-foreground/10 bg-card/80">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-5 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="text-xs tracking-[0.18em] text-primary uppercase">Capacity desk</p>
            <h1 className="font-[family-name:var(--font-serif)] text-3xl text-foreground">Sky Scout</h1>
            <p className="max-w-xl text-sm text-muted-foreground">
              Ranks US airports for added flight capacity, and answers only from the public files loaded on this desk.
              If a figure is not there, it says so.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline">{summary.airportCount} joined airports</Badge>
            <Badge variant="outline">{summary.scoreReadyCount} score-ready</Badge>
            <Badge variant="outline">Files read {builtLabel(summary.builtAt)}</Badge>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-6xl gap-4 px-4 py-4 pb-20 md:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)] md:pb-4">
        <section className={panel === "result" ? "hidden md:block" : "block"}>
          <Card className="h-full">
            <CardHeader>
              <CardTitle className="font-[family-name:var(--font-serif)] text-xl">Ask the desk</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <div className="flex flex-wrap gap-2">
                {PROMPTS.map((prompt) => (
                  <Button key={prompt} type="button" variant="outline" size="sm" className="h-auto whitespace-normal text-left" onClick={() => void ask(prompt)}>
                    {prompt}
                  </Button>
                ))}
              </div>
              <ScrollArea className="h-[46vh] rounded-lg border border-foreground/10 bg-background/60">
                <div className="flex flex-col gap-3 p-3">
                  {messages.length === 0 ? (
                    <CatalogEmpty summary={summary} />
                  ) : (
                    messages.map((message) => (
                      <div
                        key={message.id}
                        className={
                          message.role === "user"
                            ? "ml-8 rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
                            : "mr-6 rounded-lg bg-secondary px-3 py-2 text-sm"
                        }
                      >
                        {message.text}
                      </div>
                    ))
                  )}
                  {loading ? <p className="text-sm text-muted-foreground">Looking only in the loaded files…</p> : null}
                  {error ? <p className="text-sm text-destructive">{error}</p> : null}
                  <div ref={bottom} />
                </div>
              </ScrollArea>
              <form
                className="flex gap-2"
                onSubmit={(event) => {
                  event.preventDefault()
                  void ask(draft)
                }}
              >
                <Input
                  aria-label="Question"
                  value={draft}
                  placeholder="Ask about an airport, a state, freight, delay, or the capacity score"
                  onChange={(event) => setDraft(event.target.value)}
                />
                <Button type="submit" disabled={loading}>
                  Ask
                </Button>
                <Button type="button" variant="outline" aria-label={listening ? "Stop microphone" : "Start microphone"} onClick={toggleMic}>
                  {listening ? <Square /> : <Mic />}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  aria-label={speak ? "Turn off spoken replies" : "Turn on spoken replies"}
                  aria-pressed={speak}
                  onClick={() => {
                    setSpeak((value) => !value)
                    if (speak) window.speechSynthesis?.cancel()
                  }}
                >
                  {speak ? <Volume2 /> : <VolumeX />}
                </Button>
              </form>
              {voiceNote ? <p className="text-xs text-muted-foreground">{voiceNote}</p> : null}
            </CardContent>
          </Card>
        </section>

        <section className={panel === "chat" ? "hidden md:block" : "block"}>
          <Card className="h-full">
            <CardHeader>
              <CardTitle className="font-[family-name:var(--font-serif)] text-xl">
                {answer?.title ?? "Result"}
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {!answer ? (
                <p className="text-sm text-muted-foreground">
                  The result table stays empty until a question maps onto a loaded field.
                </p>
              ) : (
                <>
                  <p className="text-sm">{answer.reply}</p>
                  {answer.notes.length ? (
                    <ul className="text-xs text-muted-foreground">
                      {answer.notes.map((note) => (
                        <li key={note}>{note}</li>
                      ))}
                    </ul>
                  ) : null}
                  {answer.rows.length && answer.columns.length ? (
                    <div className="max-h-[42vh] overflow-auto rounded-lg border border-foreground/10">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            {answer.columns.map((column) => (
                              <TableHead key={column.key}>{column.label}</TableHead>
                            ))}
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {answer.rows.map((row, index) => (
                            <TableRow key={`${String(row.code ?? row.airport ?? index)}-${index}`}>
                              {answer.columns.map((column) => (
                                <TableCell key={column.key}>{formatCell(column.key, row[column.key])}</TableCell>
                              ))}
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  ) : null}
                  <Separator />
                  <div className="flex flex-col gap-2">
                    <p className="text-xs tracking-[0.14em] text-primary uppercase">Assumptions and scope</p>
                    {answer.assumptions.map((item) => (
                      <div key={item.label}>
                        <p className="text-sm font-medium">{item.label}</p>
                        <p className="text-xs text-muted-foreground">{item.detail}</p>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </section>
      </div>

      <div className="fixed inset-x-0 bottom-0 flex border-t border-foreground/10 bg-card md:hidden">
        <button type="button" className="flex-1 py-3 text-sm" onClick={() => setPanel("chat")}>
          Chat
        </button>
        <button type="button" className="flex-1 py-3 text-sm" onClick={() => setPanel("result")}>
          Result
        </button>
      </div>
    </div>
  )
}

function CatalogEmpty({ summary }: { summary: Summary }) {
  return (
    <div className="flex flex-col gap-2 text-sm text-muted-foreground">
      <p>Loaded fields, and nothing beyond them:</p>
      <ul className="list-disc pl-4">
        <li>Airport identity, elevation, and paved runways of at least 5,000 feet.</li>
        <li>2024 domestic US-carrier enplanements, passengers, departures, arrivals, freight, and mail.</li>
        <li>FAA commercial enplanements for {summary.sources.faa.years?.join(" and ") ?? "2023 and 2024"}.</li>
        <li>
          August {summary.sources.ontime.year} reporting-carrier delay rate, delay minutes, taxi-out, cancellations, and
          delay-cause minutes.
        </li>
      </ul>
      <p>
        Not loaded: stage length, seats, load factor, carriers, routes, international segments, fares, gates, and
        construction cost. Those questions get a refusal.
      </p>
      {summary.unmatchedFaa.length ? (
        <p>FAA codes left out because they did not join: {summary.unmatchedFaa.join(", ")}.</p>
      ) : null}
    </div>
  )
}
