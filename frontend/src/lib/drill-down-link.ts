import type { DrillDownFilter } from '@/components/transaction-drill-down'

/** The same filters as a link to the Transactions page. */
export function drillDownTransactionsLink(filter: DrillDownFilter) {
  const params = new URLSearchParams()
  if (filter.from) params.set('from', filter.from)
  if (filter.to) params.set('to', filter.to)
  if (filter.uncategorized) params.set('uncategorized', '1')
  else if (filter.category_id) params.set('category_id', filter.category_id)
  const accounts = filter.account_ids ?? (filter.account_id ? [filter.account_id] : [])
  if (accounts.length > 0) params.set('account_id', accounts.join(','))
  if (filter.type) params.set('type', filter.type)
  params.set('status', 'posted')
  params.set('from_screen', 'dashboard')
  return `/transactions?${params}`
}
