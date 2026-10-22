/** Pure helpers of the Trends tab: the stacked series by group and how each
 *  category moved in the last 3 months against the 3 before. */
import type { BreakdownRow } from '@/lib/api'

/** Groups drawn on their own; the rest folds into one gray layer. */
export const MAX_LAYERS = 7

export interface Layer {
  key: string
  id: string | null
  label: string
  total: number
  values: number[]
  rest?: boolean
}

/** Spending groups as stack layers, biggest first, refunds left out. */
export function buildLayers(rows: BreakdownRow[], restLabel: string): Layer[] {
  const spend = rows
    .filter((r) => r.kind === 'group' && Number(r.total) > 0 && r.sparkline.length > 0)
    .map((r) => ({
      key: r.id ?? `label:${r.label}`,
      id: r.id,
      label: r.label,
      total: Number(r.total),
      values: r.sparkline.map((v) => Math.max(0, Number(v))),
    }))
    .sort((a, b) => b.total - a.total)
  if (spend.length <= MAX_LAYERS + 1) return spend
  const head = spend.slice(0, MAX_LAYERS)
  const tail = spend.slice(MAX_LAYERS)
  const length = Math.max(...tail.map((l) => l.values.length))
  return [
    ...head,
    {
      key: '__rest',
      id: null,
      label: restLabel,
      total: tail.reduce((s, l) => s + l.total, 0),
      values: Array.from({ length }, (_, i) => tail.reduce((s, l) => s + (l.values[i] ?? 0), 0)),
      rest: true,
    },
  ]
}

export interface CategoryTrend {
  row: BreakdownRow
  /** Average per month over the last 3 months. */
  perMonth: number
  /** Last 3 months against the 3 before; null when there was nothing before. */
  change: number | null
  isNew: boolean
  /** Big enough for its change to mean something. */
  material: boolean
  values: number[]
}

const sum = (values: number[]) => values.reduce((s, v) => s + v, 0)

/** A category under this share of the recent spending is too small for its
 *  percentage to lead (a R$ 10 → R$ 40 jump is +300% and means nothing). */
export const MATERIAL_SHARE = 0.005

export function categoryTrends(rows: BreakdownRow[]): CategoryTrend[] {
  const items = rows
    .filter((r) => r.sparkline.length >= 6)
    .map((row) => {
      const values = row.sparkline.map(Number)
      const last = sum(values.slice(-3))
      const before = sum(values.slice(-6, -3))
      return {
        row,
        values,
        last,
        perMonth: last / 3,
        change: before > 0 ? (last - before) / before : null,
        isNew: before <= 0 && last > 0,
      }
    })
    .filter((i) => i.last > 0 || i.values.some((v) => v > 0))
  const recent = sum(items.map((i) => Math.max(0, i.last)))
  const out = items.map((i) => ({
    row: i.row,
    values: i.values,
    perMonth: i.perMonth,
    change: i.change,
    isNew: i.isNew,
    material: recent > 0 && i.last / recent >= MATERIAL_SHARE,
  }))
  const rank = (t: CategoryTrend) => (t.isNew ? Number.POSITIVE_INFINITY : (t.change ?? Number.NEGATIVE_INFINITY))
  return out.sort((a, b) => Number(b.material) - Number(a.material) || rank(b) - rank(a) || b.perMonth - a.perMonth)
}

/** The category that grew most (with a real base), and the one that fell most. */
export function trendExtremes(trends: CategoryTrend[]) {
  const scored = trends.filter((t) => t.material && t.change !== null)
  const up = scored.filter((t) => (t.change ?? 0) > 0).sort((a, b) => (b.change ?? 0) - (a.change ?? 0))[0]
  const down = scored.filter((t) => (t.change ?? 0) < 0).sort((a, b) => (a.change ?? 0) - (b.change ?? 0))[0]
  return { up, down }
}
