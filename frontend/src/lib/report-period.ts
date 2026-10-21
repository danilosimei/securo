/**
 * The closed periods offered by Reports (Explore, Income & Expenses, Money
 * Map) and the date range each one covers, so the screen can say "Oct/25 –
 * Sep/26, closed months" without waiting for the server.
 *
 * Mirrors backend/app/services/reports/periods.py: the rolling periods end on
 * the last day of the previous month; "this month" and "year to date" run up
 * to today and are partial.
 */
import { format } from 'date-fns'

export const REPORT_PERIODS = [
  'this_month',
  'last_month',
  'last_3m',
  'last_6m',
  'last_12m',
  'ytd',
  'custom',
] as const

export type ReportPeriodKey = (typeof REPORT_PERIODS)[number]

export const DEFAULT_REPORT_PERIOD: ReportPeriodKey = 'last_12m'

export function isReportPeriod(value: unknown): value is ReportPeriodKey {
  return typeof value === 'string' && (REPORT_PERIODS as readonly string[]).includes(value)
}

const MONTHS_BACK: Partial<Record<ReportPeriodKey, number>> = {
  last_3m: 3,
  last_6m: 6,
  last_12m: 12,
}

export interface ReportPeriodRange {
  from: string
  to: string
  /** True when the range ends today, in the middle of a month. */
  partial: boolean
}

const iso = (date: Date) => format(date, 'yyyy-MM-dd')

/** The dates `period` covers as of `today`. Custom needs its own dates. */
export function reportPeriodRange(
  period: ReportPeriodKey,
  today: Date,
  custom?: { from: string; to: string },
): ReportPeriodRange | null {
  const year = today.getFullYear()
  const month = today.getMonth()
  if (period === 'custom') return custom?.from && custom.to ? { ...custom, partial: false } : null
  if (period === 'this_month') return { from: iso(new Date(year, month, 1)), to: iso(today), partial: true }
  if (period === 'ytd') return { from: iso(new Date(year, 0, 1)), to: iso(today), partial: true }
  const lastClosedDay = new Date(year, month, 0)
  if (period === 'last_month') {
    return { from: iso(new Date(year, month - 1, 1)), to: iso(lastClosedDay), partial: false }
  }
  const back = MONTHS_BACK[period] ?? 12
  return { from: iso(new Date(year, month - back, 1)), to: iso(lastClosedDay), partial: false }
}

/** "Oct 25" style label for the first or last month of a range. */
export function monthLabel(iso: string, locale: string) {
  return new Date(iso + 'T00:00:00').toLocaleDateString(locale, { month: 'short', year: '2-digit' })
}
