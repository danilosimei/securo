/** Pure helpers of the Fixos tab: totals of the active charges and the order
 *  of the list. */
import type { RecurringRow } from '@/lib/api'

export type RecurringSort = 'year' | 'recent' | 'name'

/** What the active (not stopped) fixed costs take a month and a year, and
 *  their share of the period's average monthly spending. */
export function fixedSummary(rows: RecurringRow[], monthlySpending?: string) {
  const active = rows.filter((r) => r.status !== 'stopped')
  const month = active.reduce((s, r) => s + Number(r.amount_month), 0)
  const year = active.reduce((s, r) => s + Number(r.amount_year), 0)
  const spending = monthlySpending !== undefined ? Number(monthlySpending) : 0
  return { month, year, count: active.length, share: spending > 0 ? month / spending : null }
}

export function sortRecurring(rows: RecurringRow[], sort: RecurringSort): RecurringRow[] {
  const out = [...rows]
  if (sort === 'name') return out.sort((a, b) => a.label.localeCompare(b.label))
  if (sort === 'recent') return out.sort((a, b) => b.last_date.localeCompare(a.last_date) || a.label.localeCompare(b.label))
  // Stopped charges cost nothing any more: they sink to the bottom.
  return out.sort(
    (a, b) =>
      Number(a.status === 'stopped') - Number(b.status === 'stopped') ||
      Number(b.amount_year) - Number(a.amount_year) ||
      a.label.localeCompare(b.label),
  )
}
