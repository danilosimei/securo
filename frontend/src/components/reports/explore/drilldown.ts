/** Pure helpers for drilling down in Reports > Explore: which level a row
 *  opens and which filters list the transactions behind a store. */
import type { BreakdownRow } from '@/lib/api'

/** Labels the backend sends on the two rows that have no id. */
export const NO_GROUP_LABEL = 'Sem grupo'
export const NO_CATEGORY_LABEL = 'Sem categoria'

export type DrillStep = { group: string } | { category: string } | { merchant: string }

/** Where a click on `row` goes. Rows without an id are told apart by the
 *  label the backend sends; a category row at the group level (the refunds
 *  category) skips straight to its stores. Null when the row cannot open. */
export function nextStep(level: 'group' | 'category' | 'merchant', row: BreakdownRow): DrillStep | null {
  if (level === 'merchant') return row.id ? { merchant: row.id } : null
  if (row.id !== null) {
    if (level === 'group' && row.kind !== 'category') return { group: row.id }
    return { category: row.id }
  }
  if (level === 'group' && row.label === NO_GROUP_LABEL) return { group: 'none' }
  return { category: 'none' }
}

export interface DrilldownInput {
  range: { from: string; to: string }
  /** Category id, 'none' for the uncategorized transactions, or 'all' for
   *  every category (a store seen across all of them). */
  categoryId: string
  merchantKey: string
  page: number
  /** Only the transactions of this account. */
  accountId?: string | null
}

function categoryFilter(categoryId: string) {
  if (categoryId === 'all') return {}
  return categoryId === 'none' ? { uncategorized: true } : { category_id: categoryId }
}

export const DRILLDOWN_PAGE_SIZE = 50

/** Params of `GET /api/transactions` for the transactions of one store. */
export function drilldownTransactionParams({ range, categoryId, merchantKey, page, accountId }: DrilldownInput) {
  return {
    from: range.from,
    to: range.to,
    ...categoryFilter(categoryId),
    ...(accountId ? { account_id: accountId } : {}),
    merchant_key: merchantKey,
    status: 'posted',
    user_pnl_only: true,
    page,
    limit: DRILLDOWN_PAGE_SIZE,
  }
}

/** Same filters as a link to the Transactions page. */
export function transactionsPageLink({ range, categoryId, merchantKey, accountId }: Omit<DrilldownInput, 'page'>) {
  const params = new URLSearchParams({ from: range.from, to: range.to })
  if (categoryId === 'none') params.set('uncategorized', '1')
  else if (categoryId !== 'all') params.set('category_id', categoryId)
  if (accountId) params.set('account_id', accountId)
  params.set('merchant_key', merchantKey)
  params.set('from_screen', 'reports')
  return `/transactions?${params}`
}
