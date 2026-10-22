import { describe, expect, it } from 'vitest'

import { drilldownTransactionParams, nextStep, transactionsPageLink } from '@/components/reports/explore/drilldown'
import type { BreakdownRow } from '@/lib/api'

const row = (over: Partial<BreakdownRow>): BreakdownRow => ({
  id: 'x', label: 'X', kind: 'group', total: '1.00', share: 1, previous_total: '0.00', change: null, count: 1, sparkline: [], ...over,
})
const range = { from: '2025-10-01', to: '2026-09-30' }

describe('drilldownTransactionParams', () => {
  it('filters by category and store, posted and in the P&L', () => {
    expect(drilldownTransactionParams({ range, categoryId: 'c1', merchantKey: 'stellantis', page: 2 })).toEqual({
      from: '2025-10-01', to: '2026-09-30', category_id: 'c1', merchant_key: 'stellantis',
      status: 'posted', user_pnl_only: true, page: 2, limit: 50,
    })
  })

  it('asks for uncategorized transactions when the category is none', () => {
    const params = drilldownTransactionParams({ range, categoryId: 'none', merchantKey: 'k', page: 1 })
    expect(params).toMatchObject({ uncategorized: true })
    expect(params).not.toHaveProperty('category_id')
  })
})

describe('transactionsPageLink', () => {
  it('keeps the period, category and store', () => {
    expect(transactionsPageLink({ range, categoryId: 'c1', merchantKey: 'a b' })).toBe(
      '/transactions?from=2025-10-01&to=2026-09-30&category_id=c1&merchant_key=a+b&from_screen=reports',
    )
    expect(transactionsPageLink({ range, categoryId: 'none', merchantKey: 'k' })).toContain('uncategorized=1')
  })
})

describe('nextStep', () => {
  it('opens a group, a category and a store', () => {
    expect(nextStep('group', row({ id: 'g1' }))).toEqual({ group: 'g1' })
    expect(nextStep('category', row({ id: 'c1', kind: 'category' }))).toEqual({ category: 'c1' })
    expect(nextStep('merchant', row({ id: 'loja', kind: 'merchant' }))).toEqual({ merchant: 'loja' })
  })

  it('sends the no-group row to the categories without group', () => {
    expect(nextStep('group', row({ id: null, label: 'Sem grupo' }))).toEqual({ group: 'none' })
  })

  it('sends the no-category row straight to the stores', () => {
    expect(nextStep('group', row({ id: null, label: 'Sem categoria' }))).toEqual({ category: 'none' })
  })

  it('sends a category row at the group level (refunds) straight to the stores', () => {
    expect(nextStep('group', row({ id: 'c9', kind: 'category', label: 'Estornos' }))).toEqual({ category: 'c9' })
  })
})
