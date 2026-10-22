/** The sentence the Overview opens with, built from the numbers: how much
 *  was spent, against what came before, and what was left or missing. */
import type { TFunction } from 'i18next'
import type { OverviewResponse } from '@/lib/api'
import { formatMoneyShort, formatPercent, monthName, monthsBetween } from '@/components/reports/common/chart-utils'

interface HeadlineInput {
  data: OverviewResponse
  /** The same report over the previous period, when it is comparable. */
  previous?: OverviewResponse | null
  currency: string
  locale: string
  t: TFunction
  mask: (value: string) => string
}

/** Relative change of spending against the previous period, or null when
 *  there is nothing fair to compare with (a period still running, no base). */
export function spendingChange(data: OverviewResponse, previous?: OverviewResponse | null): number | null {
  if (!previous || data.period.partial) return null
  const now = Number(data.kpis.spending)
  const before = Number(previous.kpis.spending)
  if (!(before > 0)) return null
  return (now - before) / before
}

export function overviewHeadline({ data, previous, currency, locale, t, mask }: HeadlineInput): string | null {
  const spending = Number(data.kpis.spending)
  const income = Number(data.kpis.income)
  const left = Number(data.kpis.left)
  if (spending === 0 && income === 0) return null

  const months = monthsBetween(data.period.from, data.period.to)
  const single = months.length === 1
  const money = (v: number) => mask(formatMoneyShort(v, currency, locale))
  const change = spendingChange(data, previous)
  // Under half a percent reads as "the same" and is not worth a clause.
  const direction = change === null || Math.abs(change) < 0.005 ? '' : change > 0 ? 'Up' : 'Down'

  const base = single ? (data.period.partial ? 'monthPartial' : 'month') : 'average'
  const main = t(`reports.overview.headline.${base}${data.period.partial ? '' : direction}`, {
    month: monthName(data.period.from, locale),
    from: monthName(data.period.from, locale, 'short'),
    to: monthName(data.period.to, locale, 'short'),
    spending: money(spending),
    change: change === null ? '' : formatPercent(Math.abs(change), locale),
    previous: monthName(data.period.previous_from, locale),
    months: months.length,
  })

  const per = single ? '' : 'Monthly'
  const tail =
    income <= 0
      ? t('reports.overview.headline.end')
      : left >= 0
        ? t(`reports.overview.headline.left${per}`, { left: money(left) })
        : t(`reports.overview.headline.short${per}`, { left: money(-left) })
  return `${main}${tail}`
}
