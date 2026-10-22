/** Reports > Hábitos: how the money goes out, not just where. Each block
 *  answers one question in plain words: where you buy all the time, where
 *  each purchase costs most, which day weighs most, and how much your
 *  purchases usually cost. */
import { useTranslation } from 'react-i18next'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { ShoppingBag } from 'lucide-react'
import { reports, type HabitsMerchant } from '@/lib/api'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useReportedRange } from '@/components/reports/common/report-range'
import { ReportBlock } from '@/components/reports/common/report-block'
import { AnswerLine } from '@/components/reports/common/answer-line'
import { answerParts } from '@/components/reports/common/answer-parts'
import { blockState } from '@/components/reports/common/block-state'
import { formatMoneyShort } from '@/components/reports/common/chart-utils'
import { useGroupPalette } from '@/components/reports/common/group-colors'
import { exploreLink, useReportPeriod } from '@/components/reports/common/report-url'
import { BandBars, WeekdayBars } from './habit-bars'
import { HabitList } from './habit-lists'
import { bandLabel, habitRanking, smallPurchases, totals, weekdayName } from './habits-model'

export function HabitsTab() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const navigate = useNavigate()
  const palette = useGroupPalette()
  const { query } = useReportPeriod()

  const habits = useQuery({
    queryKey: ['reports', 'habits', query],
    queryFn: () => reports.habits(query),
    placeholderData: keepPreviousData,
  })
  const data = habits.data
  const stale = habits.isPlaceholderData
  const currency = user?.preferences?.currency_display ?? 'BRL'
  const money = (v: number) => mask(formatMoneyShort(v, currency, locale))
  const times = (perMonth: number) => perMonth.toLocaleString(locale, { maximumFractionDigits: perMonth < 1 ? 1 : 0 })

  const groupOf = (m: HabitsMerchant) => (m.category_id ? palette.groupOfCategory(m.category_id) : null)
  const colorOf = (m: HabitsMerchant) => palette.group(groupOf(m))
  const merchantLink = (m: HabitsMerchant) => {
    const groupId = groupOf(m)
    return exploreLink(
      { ...query, group: groupId, category: m.category_id ?? 'none', merchant: m.id },
      { group: groupId ? palette.groupName(groupId) : undefined, category: m.category, merchant: m.label },
      data ? { from: data.period.from, to: data.period.to } : undefined,
    )
  }
  const openMerchant = (m: HabitsMerchant) => {
    const link = merchantLink(m)
    navigate(link.to, { state: link.state })
  }

  const sum = data ? totals(data) : null
  const months = data
    ? Math.max(1, Math.round((new Date(`${data.period.to}T00:00:00`).getTime() - new Date(`${data.period.from}T00:00:00`).getTime()) / (30.44 * 86400000)))
    : 1
  const often = data ? habitRanking(data.merchants, 'often')[0] : undefined
  const pricey = data ? habitRanking(data.merchants, 'pricey')[0] : undefined

  const headline = !data || !sum || sum.count === 0
    ? null
    : often
      ? answerParts(t, 'reports.habits.headline', {
          n: { kind: 'value', text: sum.count.toLocaleString(locale) },
          perMonth: { kind: 'value', text: Math.round(sum.count / months).toLocaleString(locale) },
          name: { kind: 'link', text: often.label, ...merchantLink(often) },
          freq: { kind: 'value', text: times(often.per_month) },
        })
      : answerParts(t, 'reports.habits.headlineCount', { n: { kind: 'value', text: sum.count.toLocaleString(locale) } })

  const topDay = data?.weekdays.length
    ? data.weekdays.reduce((best, d) => (Number(d.total) > Number(best.total) ? d : best), data.weekdays[0])
    : null
  const commonBand = data?.bands.length
    ? data.bands.reduce((best, b) => (b.count > best.count ? b : best), data.bands[0])
    : null
  const small = data ? smallPurchases(data.bands) : null

  const common = { stale, onRetry: () => habits.refetch() }
  const noPurchases = sum?.count === 0
  useReportedRange(data?.period)

  return (
    <div className="space-y-5" data-testid="habits-tab">
      <AnswerLine parts={headline} icon={ShoppingBag} loading={!data && !habits.isError} stale={stale} />

      <div className="grid gap-5 lg:grid-cols-2">
        <ReportBlock
          {...common}
          question={often ? t('reports.habits.often.question') : undefined}
          title={
            often
              ? t('reports.habits.often.answer', { name: often.label, n: times(often.per_month) })
              : t('reports.habits.often.question')
          }
          description={t('reports.habits.often.description')}
          state={blockState(habits, (data?.merchants.length ?? 0) === 0)}
          emptyText={t('reports.habits.empty')}
          skeletonHeight={280}
          testId="block-often"
        >
          {data && (
            <HabitList merchants={data.merchants} measure="often" currency={currency} locale={locale} colorOf={colorOf} onOpen={openMerchant} testId="habit-often" />
          )}
        </ReportBlock>
        <ReportBlock
          {...common}
          question={pricey ? t('reports.habits.pricey.question') : undefined}
          title={
            pricey
              ? t('reports.habits.pricey.answer', { name: pricey.label, value: money(Number(pricey.avg_ticket)) })
              : t('reports.habits.pricey.question')
          }
          description={t('reports.habits.pricey.description')}
          state={blockState(habits, (data?.merchants.length ?? 0) === 0)}
          emptyText={t('reports.habits.empty')}
          skeletonHeight={280}
          testId="block-pricey"
        >
          {data && (
            <HabitList merchants={data.merchants} measure="pricey" currency={currency} locale={locale} colorOf={colorOf} onOpen={openMerchant} testId="habit-pricey" />
          )}
        </ReportBlock>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <ReportBlock
          {...common}
          question={topDay ? t('reports.habits.weekdays.question') : undefined}
          title={topDay ? t('reports.habits.weekdays.answer', { day: weekdayName(topDay.weekday, locale, 'long') }) : t('reports.habits.weekdays.question')}
          description={
            topDay
              ? t('reports.habits.weekdays.detail', { total: money(Number(topDay.total)), n: topDay.count.toLocaleString(locale) })
              : undefined
          }
          state={blockState(habits, (data?.weekdays.length ?? 0) === 0 || noPurchases)}
          emptyText={t('reports.habits.empty')}
          testId="block-weekdays"
        >
          {data && <WeekdayBars weekdays={data.weekdays} currency={currency} locale={locale} />}
        </ReportBlock>
        <ReportBlock
          {...common}
          question={commonBand ? t('reports.habits.bands.question') : undefined}
          title={
            commonBand
              ? t('reports.habits.bands.answer', { band: bandLabel(commonBand, currency, locale, t) })
              : t('reports.habits.bands.question')
          }
          description={
            small && small.count > 0
              ? t('reports.habits.bands.detail', {
                  n: small.count.toLocaleString(locale),
                  limit: formatMoneyShort(small.limit, currency, locale),
                  total: money(small.total),
                })
              : undefined
          }
          state={blockState(habits, (data?.bands.length ?? 0) === 0 || noPurchases)}
          emptyText={t('reports.habits.empty')}
          testId="block-bands"
        >
          {data && <BandBars bands={data.bands} currency={currency} locale={locale} />}
        </ReportBlock>
      </div>
    </div>
  )
}
