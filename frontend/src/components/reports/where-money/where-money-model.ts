/** Slices of the "Para onde foi o dinheiro" donut: only what took money
 *  (refunds stay out), biggest first, and past the last slice everything
 *  folds into one "Others" slice so the donut never turns into confetti. */
import type { BreakdownRow, ReportPeriodParams } from '@/lib/api'
import { monthRange } from '@/lib/month-utils'

export const MAX_SLICES = 7

export interface Slice {
  key: string
  label: string
  value: number
  /** Ratio of the money the slices add up to. */
  share: number
  /** Null on the folded "Others" slice, which does not open. */
  row: BreakdownRow | null
}

export function buildSlices(rows: BreakdownRow[], othersLabel: string, max = MAX_SLICES): Slice[] {
  const positive = rows
    .map((row) => ({ row, value: Number(row.total) }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value)
  const sum = positive.reduce((s, r) => s + r.value, 0)
  if (sum === 0) return []
  const kept = positive.length > max ? positive.slice(0, max - 1) : positive
  const slices: Slice[] = kept.map(({ row, value }) => ({
    key: row.id ?? row.label,
    label: row.label,
    value,
    share: value / sum,
    row,
  }))
  if (kept.length < positive.length) {
    const rest = positive.slice(kept.length).reduce((s, r) => s + r.value, 0)
    slices.push({ key: 'rest', label: othersLabel, value: rest, share: rest / sum, row: null })
  }
  return slices
}

/** The period of the Dashboard's month (YYYY-MM) for the reports API, which
 *  refuses a custom range that ends after today: the current month is "this
 *  month" (up to today), a past month its whole range, and a month still to
 *  come has nothing to ask for (null). */
export function monthScope(month: string, current: string): ReportPeriodParams | null {
  if (month === current) return { period: 'this_month' }
  if (month > current) return null
  return { period: 'custom', ...monthRange(month) }
}
