/** Pure helpers of Explore's ways to look at the spending: what the URL
 *  asks for, how concentrated a list is, and the colors of accounts. */
import type { BreakdownCompare, BreakdownRow } from '@/lib/api'
import { NEUTRAL_COLOR } from '@/components/reports/common/group-colors'
import { monthName } from '@/components/reports/common/chart-utils'

export const EXPLORE_VIEWS = ['group', 'merchant', 'month', 'account', 'compare'] as const
export type ExploreView = (typeof EXPLORE_VIEWS)[number]

export const EXPLORE_CHARTS = ['bars', 'treemap', 'trend'] as const
export type ExploreChart = (typeof EXPLORE_CHARTS)[number]

export const COMPARE_MODES: BreakdownCompare[] = ['previous', 'year_ago', 'custom']

/** The views that rank rows and can be drawn as bars, blocks or lines. */
export const RANKING_VIEWS: ExploreView[] = ['group', 'merchant', 'account']

export function parseView(value: string | null): ExploreView {
  return (EXPLORE_VIEWS as readonly string[]).includes(value ?? '') ? (value as ExploreView) : 'group'
}

export function parseChart(value: string | null): ExploreChart {
  return (EXPLORE_CHARTS as readonly string[]).includes(value ?? '') ? (value as ExploreChart) : 'bars'
}

export function parseCompare(value: string | null): BreakdownCompare {
  return (COMPARE_MODES as string[]).includes(value ?? '') ? (value as BreakdownCompare) : 'previous'
}

/** How many of the biggest rows make 80% of the total. Null when it would
 *  say nothing: few rows, or no clear head (more than half of them). */
export const CONCENTRATION_SHARE = 0.8
export const CONCENTRATION_MIN_ROWS = 6

export function concentration(rows: BreakdownRow[]): { n: number; m: number } | null {
  const values = rows.map((r) => Number(r.total)).filter((v) => v > 0).sort((a, b) => b - a)
  const m = values.length
  if (m < CONCENTRATION_MIN_ROWS) return null
  const total = values.reduce((s, v) => s + v, 0)
  let sum = 0
  let n = 0
  for (const v of values) {
    sum += v
    n += 1
    if (sum >= total * CONCENTRATION_SHARE) break
  }
  return n <= m / 2 ? { n, m } : null
}

/** Lines of the "Evolução" chart: the 5 biggest rows and the rest summed. */
export const TREND_LINES = 5

export interface TrendSeries {
  key: string
  label: string
  values: number[]
  row?: BreakdownRow
  rest?: boolean
}

export function trendSeries(rows: BreakdownRow[], restLabel: string): TrendSeries[] {
  const positive = rows.filter((r) => Number(r.total) > 0 && r.sparkline.length > 0)
  const head = positive.slice(0, TREND_LINES)
  const tail = positive.slice(TREND_LINES)
  const series: TrendSeries[] = head.map((row) => ({
    key: row.id ?? `label:${row.label}`,
    label: row.label,
    values: row.sparkline.map((v) => Number(v)),
    row,
  }))
  if (tail.length > 0) {
    const length = Math.max(...tail.map((r) => r.sparkline.length))
    series.push({
      key: '__rest',
      label: restLabel,
      values: Array.from({ length }, (_, i) => tail.reduce((s, r) => s + Number(r.sparkline[i] ?? 0), 0)),
      rest: true,
    })
  }
  return series
}

/** A color per account that does not change with the period or the rank:
 *  accounts in id order take the palette in its order; past its eight
 *  colors, the rest are neutral (never a cycled or made-up hue). */
/** Slots of the categorical palette (`--cat-1`..`--cat-8`, light and dark). */
export const ACCOUNT_SLOTS = 8

export function accountColors(ids: string[]): Map<string, string> {
  const out = new Map<string, string>()
  ;[...ids].sort().forEach((id, i) => out.set(id, i < ACCOUNT_SLOTS ? `var(--cat-${i + 1})` : NEUTRAL_COLOR))
  return out
}

/** One month (YYYY-MM) as a custom period. */
export function monthRange(month: string) {
  const [year, mon] = month.split('-').map(Number)
  const last = new Date(year, mon, 0).getDate()
  return { from: `${month}-01`, to: `${month}-${String(last).padStart(2, '0')}` }
}

/** The 5 color steps of the month table: 0 is no spending; 1..5 grow with
 *  the value, linear on the biggest cell. */
export const HEAT_STEPS = 5

export function heatStep(value: number, max: number) {
  if (!(value > 0) || !(max > 0)) return 0
  return Math.min(HEAT_STEPS, Math.max(1, Math.ceil((value / max) * HEAT_STEPS)))
}

function isMonthStart(iso: string) {
  return iso.endsWith('-01')
}

function isMonthEnd(iso: string) {
  const d = new Date(`${iso}T00:00:00`)
  d.setDate(d.getDate() + 1)
  return d.getDate() === 1
}

/** "set/25 – ago/26" for whole months, else the days. */
export function rangeLabel(from: string, to: string, locale: string) {
  if (isMonthStart(from) && isMonthEnd(to)) {
    const a = monthName(from, locale, 'short')
    const b = monthName(to, locale, 'short')
    return a === b ? a : `${a} – ${b}`
  }
  const day = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(locale, { day: '2-digit', month: '2-digit', year: '2-digit' })
  return `${day(from)} – ${day(to)}`
}
