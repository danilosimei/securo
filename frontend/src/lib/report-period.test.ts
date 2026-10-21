import { describe, expect, it } from 'vitest'
import { isReportPeriod, reportPeriodRange } from '@/lib/report-period'

const today = new Date(2026, 9, 21) // Oct 21, 2026

describe('reportPeriodRange', () => {
  it('12 months ends on the last closed month', () => {
    expect(reportPeriodRange('last_12m', today)).toEqual({ from: '2025-10-01', to: '2026-09-30', partial: false })
  })

  it('3 and 6 months count back from the last closed month', () => {
    expect(reportPeriodRange('last_3m', today)).toEqual({ from: '2026-07-01', to: '2026-09-30', partial: false })
    expect(reportPeriodRange('last_6m', today)?.from).toBe('2026-04-01')
  })

  it('last month is the previous calendar month', () => {
    expect(reportPeriodRange('last_month', new Date(2026, 0, 5))).toEqual({
      from: '2025-12-01',
      to: '2025-12-31',
      partial: false,
    })
  })

  it('this month and the current year run up to today and are partial', () => {
    expect(reportPeriodRange('this_month', today)).toEqual({ from: '2026-10-01', to: '2026-10-21', partial: true })
    expect(reportPeriodRange('ytd', today)).toEqual({ from: '2026-01-01', to: '2026-10-21', partial: true })
  })

  it('custom needs both dates', () => {
    expect(reportPeriodRange('custom', today)).toBeNull()
    expect(reportPeriodRange('custom', today, { from: '2026-01-01', to: '2026-02-01' })?.to).toBe('2026-02-01')
  })
})

it('isReportPeriod rejects the old range keys', () => {
  expect(isReportPeriod('last_6m')).toBe(true)
  for (const old of ['1y', '2y', '30d', '6m']) expect(isReportPeriod(old)).toBe(false)
})
