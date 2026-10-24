/** The sentence the Overview opens with, built from the numbers: how much
 *  was spent, against what came before, and what was left or missing. */
import type { TFunction } from 'i18next'
import type { OverviewResponse } from '@/lib/api'
import { formatMoneyShort, monthName, monthsBetween } from '@/components/reports/common/chart-utils'
import { answerParts, type AnswerPart } from '@/components/reports/common/answer-parts'

interface HeadlineInput {
  data: OverviewResponse
  currency: string
  locale: string
  t: TFunction
  mask: (value: string) => string
}

/** Relative change of spending against the previous period, or null when
 *  there is nothing fair to compare with (a period still running, no base). */
export function spendingChange(data: OverviewResponse): number | null {
  const previous = data.kpis_previous
  if (!previous || data.period.partial) return null
  const now = Number(data.kpis.spending)
  const before = Number(previous.spending)
  if (!(before > 0)) return null
  return (now - before) / before
}

export function overviewHeadline({ data, currency, locale, t, mask }: HeadlineInput): AnswerPart[] | null {
  const spending = Number(data.kpis.spending)
  const income = Number(data.kpis.income)
  const left = Number(data.kpis.left)
  if (spending === 0 && income === 0) return null

  const months = monthsBetween(data.period.from, data.period.to)
  const single = months.length === 1
  const value = (v: number) => ({ kind: 'value' as const, text: mask(formatMoneyShort(v, currency, locale)) })
  const change = spendingChange(data)
  // Under half a percent reads as "the same" and is not worth a clause.
  const direction = change === null || Math.abs(change) < 0.005 ? '' : change > 0 ? 'Up' : 'Down'

  const base = single ? (data.period.partial ? 'monthPartial' : 'month') : 'average'
  const main = answerParts(t, `reports.overview.headline.${base}${data.period.partial ? '' : direction}`, {
    month: monthName(data.period.from, locale),
    from: monthName(data.period.from, locale, 'short'),
    to: monthName(data.period.to, locale, 'short'),
    spending: value(spending),
    // Spending going down is the good direction.
    change: { kind: 'delta', change: change ?? 0, good: (change ?? 0) < 0, locale },
    previous: monthName(data.period.previous_from, locale),
    months: months.length,
  })

  const per = single ? '' : 'Monthly'
  const tail =
    income <= 0
      ? answerParts(t, 'reports.overview.headline.end', {})
      : left >= 0
        ? answerParts(t, `reports.overview.headline.left${per}`, { left: value(left) })
        : answerParts(t, `reports.overview.headline.short${per}`, { left: value(-left) })
  return [...main, ...tail]
}
