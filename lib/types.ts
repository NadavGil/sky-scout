export type NtadActivity = {
  year: number | null
  enplanements: number | null
  passengers: number | null
  departures: number | null
  arrivals: number | null
  freightLb: number | null
  mail: number | null
}

export type FaaActivity = {
  enplanements2024: number | null
  enplanements2023: number | null
  yoy: number | null
  hub: string | null
  serviceLevel: string | null
}

export type OnTimeActivity = {
  flights: number
  cancelled: number
  operated: number
  depDelay15Rate: number | null
  depDelay15N: number
  meanDepDelayMinutes: number | null
  meanTaxiOutMinutes: number | null
  cancellationRate: number | null
  carrierDelayMinutes: number
  weatherDelayMinutes: number
  nasDelayMinutes: number
  securityDelayMinutes: number
  lateAircraftDelayMinutes: number
}

export type Airport = {
  iata: string
  name: string
  city: string
  state: string
  country: string
  elevationFt: number | null
  lat: number | null
  lon: number | null
  scheduledService: boolean
  airportType: string
  longRunways: number
  ntad: NtadActivity | null
  faa: FaaActivity | null
  ontime: OnTimeActivity | null
}

export type SourceNote = {
  url?: string
  airports?: string
  runways?: string
  year?: number
  month?: number
  years?: number[]
  origins?: number
  rows?: number
  mode: string
  note?: string
}

export type Catalog = {
  builtAt: string
  sources: {
    ourairports: SourceNote
    ntad: SourceNote
    faa: SourceNote
    ontime: SourceNote
  }
  airports: Record<string, Airport>
  unmatched: {
    faa: string[]
    ntad: string[]
  }
}

export type Metric =
  | "score"
  | "enplanements"
  | "passengers"
  | "departures"
  | "arrivals"
  | "freight"
  | "mail"
  | "yoy"
  | "delayRate"
  | "delayMinutes"
  | "taxiOut"
  | "cancellationRate"
  | "longRunways"
  | "delayCause"
  | "elevation"

export type Intent =
  | {
      kind: "refuse"
      lookedFor: string
      reason: string
    }
  | {
      kind: "clarify"
      question: string
      options: Array<{ code: string; name: string; city: string; state: string }>
    }
  | {
      kind: "rank"
      states: string[] | null
      metric: Metric
      terminal: boolean
      congestion: boolean
      limit: number | null
      groupBy: "airport" | "state"
      notes: string[]
    }
  | {
      kind: "compare"
      codes: string[]
      metric: Metric
      terminal: boolean
      congestion: boolean
      notes: string[]
    }
  | {
      kind: "airport"
      code: string
      metric: Metric
      terminal: boolean
      congestion: boolean
      notes: string[]
    }

export type Assumption = {
  label: string
  detail: string
}

export type ResultRow = Record<string, string | number | boolean | null>

export type QueryResult = {
  title: string
  reply: string
  speechText: string
  refusal: boolean
  columns: Array<{ key: string; label: string }>
  rows: ResultRow[]
  assumptions: Assumption[]
  notes: string[]
}
