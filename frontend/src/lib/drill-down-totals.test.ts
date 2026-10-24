import { describe, expect, it } from 'vitest'

import { drillDownServerTotal } from '@/lib/drill-down-totals'

const summary = { income: 300, expense: 1000 }

describe('drillDownServerTotal', () => {
  it('reads a category or expenses click as expense minus income', () => {
    expect(drillDownServerTotal(summary, { type: 'debit' })).toBe(700)
    expect(drillDownServerTotal(summary, {})).toBe(700)
  })

  it('reads an income click as income minus expense', () => {
    expect(drillDownServerTotal({ income: 5000, expense: 200 }, { type: 'credit' })).toBe(4800)
  })

  it('counts only the expense for uncategorized', () => {
    expect(drillDownServerTotal(summary, { uncategorized: true })).toBe(1000)
  })

  it('accepts decimal strings from the API', () => {
    const fromApi = { income: '10.50', expense: '30.25' } as unknown as { income: number; expense: number }
    expect(drillDownServerTotal(fromApi, {})).toBeCloseTo(19.75)
  })
})
