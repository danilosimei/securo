import type { TransactionsSummary } from '@/types'

/** The slice of the panel's filter that decides which way a total reads. */
export type DrillDownTotalKind = {
  type?: 'credit' | 'debit'
  uncategorized?: boolean
}

/**
 * The total the Dashboard drill-down shows, taken from the server's `summary`
 * of the whole filtered set (not just the page on screen), with the sign each
 * click has always had:
 *
 * - income click (`credit`): income minus expense;
 * - uncategorized: expense only, since a credit without a category is income
 *   to check, not a refund of anything (same rule as Reports > Explore);
 * - everything else (category, expenses, a day): expense minus income, so a
 *   refund reduces what was spent.
 *
 * Only posted rows are in the set, matching the Dashboard cards; planned
 * (projected) rows are listed apart and never enter it.
 */
export function drillDownServerTotal(
  summary: Pick<TransactionsSummary, 'income' | 'expense'>,
  kind: DrillDownTotalKind,
): number {
  const income = Number(summary.income)
  const expense = Number(summary.expense)
  if (kind.uncategorized) return expense
  if (kind.type === 'credit') return income - expense
  return expense - income
}
