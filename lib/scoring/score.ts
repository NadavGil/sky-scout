export const SCORE_WEIGHTS = {
  pressure: 0.32,
  scale: 0.24,
  yoy: 0.24,
  delay: 0.2,
} as const

export type ScoreInputs = {
  pressure: number
  scale: number
  yoy: number
  delayRate: number
  enplanements: number
  departures: number
  longRunways: number
}

export function percentileRanks(values: number[]): number[] {
  const count = values.length
  const ranks = new Array<number>(count).fill(0)
  if (count === 0) return ranks
  const order = values.map((value, index) => ({ value, index })).sort((a, b) => a.value - b.value)
  let cursor = 0
  while (cursor < count) {
    let end = cursor
    while (end < count && order[end].value === order[cursor].value) end += 1
    const averageRank = (cursor + 1 + end) / 2
    const percentile = count === 1 ? 100 : ((averageRank - 1) / (count - 1)) * 100
    for (let index = cursor; index < end; index += 1) ranks[order[index].index] = percentile
    cursor = end
  }
  return ranks
}

export function quantile(sortedAscending: number[], percentile: number): number | null {
  if (sortedAscending.length === 0) return null
  if (sortedAscending.length === 1) return sortedAscending[0]
  const position = percentile * (sortedAscending.length - 1)
  const lower = Math.floor(position)
  const upper = Math.ceil(position)
  if (lower === upper) return sortedAscending[lower]
  const weight = position - lower
  return sortedAscending[lower] * (1 - weight) + sortedAscending[upper] * weight
}

export function compositeScore(parts: {
  pressurePercentile: number
  scalePercentile: number
  yoyPercentile: number
  delayPercentile: number
}): number {
  return (
    SCORE_WEIGHTS.pressure * parts.pressurePercentile +
    SCORE_WEIGHTS.scale * parts.scalePercentile +
    SCORE_WEIGHTS.yoy * parts.yoyPercentile +
    SCORE_WEIGHTS.delay * parts.delayPercentile
  )
}
