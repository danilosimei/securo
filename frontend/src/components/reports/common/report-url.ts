/** URL state shared by the Reports tabs: the period every tab reads from
 *  `?period=` (plus `from`/`to` for a custom range) and the links that send a
 *  click on a chart to Explore, Fixos or Transactions. */
import { useLocation, useSearchParams } from 'react-router-dom'
import type { ReportPeriod, ReportPeriodParams } from '@/lib/api'
import { DEFAULT_REPORT_PERIOD, isReportPeriod } from '@/lib/report-period'

/** Where a chart click lands in Explore. A store needs its category: without
 *  one the click opens the store's transactions instead. */
export interface ExploreTarget {
  period: ReportPeriod
  from?: string
  to?: string
  group?: string | null
  category?: string | null
  merchant?: string | null
}

/** Names of the levels a link opens, for Explore's breadcrumb. */
export interface ExploreLabels {
  group?: string
  category?: string
  merchant?: string
}

export interface ReportLink {
  to: string
  state?: { exploreLabels: ExploreLabels }
}

function periodParams(params: URLSearchParams, period: ReportPeriod, from?: string, to?: string) {
  params.set('period', period)
  if (period === 'custom' && from && to) {
    params.set('from', from)
    params.set('to', to)
  }
}

/** Link to Explore at the level `target` names. `range` is the period's real
 *  dates, used when a store without category falls back to Transactions. */
export function exploreLink(
  target: ExploreTarget,
  labels: ExploreLabels = {},
  range?: { from: string; to: string },
): ReportLink {
  if (target.merchant && !target.category) {
    const params = new URLSearchParams()
    if (range) {
      params.set('from', range.from)
      params.set('to', range.to)
    }
    params.set('merchant_key', target.merchant)
    return { to: `/transactions?${params}` }
  }
  const params = new URLSearchParams({ tab: 'explore' })
  periodParams(params, target.period, target.from, target.to)
  if (target.group) params.set('group', target.group)
  if (target.category) params.set('category', target.category)
  if (target.category && target.merchant) params.set('merchant', target.merchant)
  return { to: `/reports?${params}`, state: { exploreLabels: labels } }
}

/** Explore over one calendar month (YYYY-MM), as a custom period. */
export function monthExploreLink(month: string): ReportLink {
  const [year, mon] = month.split('-').map(Number)
  const last = new Date(year, mon, 0).getDate()
  return exploreLink({
    period: 'custom',
    from: `${month}-01`,
    to: `${month}-${String(last).padStart(2, '0')}`,
  })
}

/** Another Reports tab over the same period. */
export function tabLink(tab: string, query: ReportPeriodParams): ReportLink {
  const params = new URLSearchParams({ tab })
  periodParams(params, query.period, query.from, query.to)
  return { to: `/reports?${params}` }
}

/** The period of the Reports tabs, read from and written to the URL. A
 *  custom period without both dates falls back to the default. */
export function useReportPeriod() {
  const [searchParams, setSearchParams] = useSearchParams()
  const location = useLocation()
  const requested = searchParams.get('period')
  const from = searchParams.get('from') ?? ''
  const to = searchParams.get('to') ?? ''
  const wanted: ReportPeriod = isReportPeriod(requested) ? requested : DEFAULT_REPORT_PERIOD
  const period: ReportPeriod = wanted === 'custom' && !(from && to) ? DEFAULT_REPORT_PERIOD : wanted
  const query: ReportPeriodParams = period === 'custom' ? { period, from, to } : { period }

  const update = (next: ReportPeriod, range?: { from: string; to: string }) =>
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev)
        params.set('period', next)
        if (range) {
          params.set('from', range.from)
          params.set('to', range.to)
        } else {
          params.delete('from')
          params.delete('to')
        }
        return params
      },
      // Keep what the view carries (Explore's breadcrumb names).
      { replace: true, state: location.state },
    )

  return { period, from, to, query, update }
}
