import { describe, expect, it } from 'vitest'

import { drillDownTransactionsLink } from '@/lib/drill-down-link'

describe('drillDownTransactionsLink', () => {
  it('carries the panel filters to Transactions and says they came from the Dashboard', () => {
    expect(
      drillDownTransactionsLink({ title: 'Food', category_id: 'c1', from: '2026-10-01', to: '2026-10-31', type: 'debit', account_ids: ['a', 'b'] }),
    ).toBe('/transactions?from=2026-10-01&to=2026-10-31&category_id=c1&account_id=a%2Cb&type=debit&status=posted&from_screen=dashboard')
  })

  it('uses uncategorized instead of a category', () => {
    expect(drillDownTransactionsLink({ title: 'x', uncategorized: true })).toBe(
      '/transactions?uncategorized=1&status=posted&from_screen=dashboard',
    )
  })
})
