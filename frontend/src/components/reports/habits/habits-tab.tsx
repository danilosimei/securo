/** Reports > Hábitos: how the money goes out, not just where. Where you
 *  return most and how much each visit costs, which weekday weighs most, how
 *  much the small purchases add up to, and the calendar of every day. */
import { useTranslation } from 'react-i18next'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { reports, type HabitsMerchant } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useReportedRange } from '@/components/reports/common/report-range'
import { Headline, ReportBlock } from '@/components/reports/common/report-block'
import { blockState } from '@/components/reports/common/block-state'
import { formatMoneyShort } from '@/components/reports/common/chart-utils'
import { useGroupPalette } from '@/components/reports/common/group-colors'
import { exploreLink, useReportPeriod } from '@/components/reports/common/report-url'
import { BubbleChart } from './bubble-chart'
import { BandBars, WeekdayBars } from './habit-bars'
import { CalendarHeatmap } from './calendar-heatmap'
import { costliestHabit, smallPurchases, totals, weekdayName } from './habits-model'

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
  const full = (v: number) => mask(formatCurrency(v, currency, locale))

  const sum = data ? totals(data) : null
  const habit = data ? costliestHabit(data.merchants) : null
  const frequent = data ? [...data.merchants].sort((a, b) => b.per_month - a.per_month)[0] : undefined
  const months = data
    ? Math.max(1, Math.round((new Date(`${data.period.to}T00:00:00`).getTime() - new Date(`${data.period.from}T00:00:00`).getTime()) / (30.44 * 86400000)))
    : 1

  const headline = !data || !sum || sum.count === 0
    ? null
    : frequent
      ? t('reports.habits.headline', {
          n: sum.count.toLocaleString(locale),
          perMonth: Math.round(sum.count / months).toLocaleString(locale),
          name: frequent.label,
          freq: frequent.per_month.toLocaleString(locale, { maximumFractionDigits: 1 }),
        })
      : t('reports.habits.headlineCount', { n: sum.count.toLocaleString(locale) })

  const bubblesAnswer = habit
    ? t('reports.habits.bubbles.answer', {
        name: habit.label,
        perMonth: habit.per_month.toLocaleString(locale, { maximumFractionDigits: 1 }),
        ticket: money(Number(habit.avg_ticket)),
        total: money(Number(habit.total)),
      })
    : ''
  const topDay = data?.weekdays.length
    ? data.weekdays.reduce((best, d) => (Number(d.total) > Number(best.total) ? d : best), data.weekdays[0])
    : null
  const weekdayAnswer = topDay
    ? t('reports.habits.weekdays.answer', {
        day: weekdayName(topDay.weekday, locale, 'long'),
        total: money(Number(topDay.total)),
        n: topDay.count.toLocaleString(locale),
      })
    : ''
  const small = data ? smallPurchases(data.bands) : null
  const bandsAnswer = small && small.count > 0
    ? t('reports.habits.bands.answer', {
        n: small.count.toLocaleString(locale),
        limit: formatMoneyShort(small.limit, currency, locale),
        total: money(small.total),
      })
    : ''
  const topDayCell = data?.days.length
    ? data.days.reduce((best, d) => (Number(d.total) > Number(best.total) ? d : best), data.days[0])
    : null
  const calendarAnswer = topDayCell && Number(topDayCell.total) > 0
    ? t('reports.habits.calendar.answer', {
        day: new Date(`${topDayCell.date}T00:00:00`).toLocaleDateString(locale, { day: 'numeric', month: 'long', year: 'numeric' }),
        total: full(Number(topDayCell.total)),
      })
    : ''

  const groupOf = (m: HabitsMerchant) => (m.category_id ? palette.groupOfCategory(m.category_id) : null)
  const colorOf = (m: HabitsMerchant) => palette.group(groupOf(m))
  const openMerchant = (m: HabitsMerchant) => {
    const groupId = groupOf(m)
    const link = exploreLink(
      { ...query, group: groupId, category: m.category_id ?? 'none', merchant: m.id },
      { group: groupId ? palette.groupName(groupId) : undefined, category: m.category, merchant: m.label },
      data ? { from: data.period.from, to: data.period.to } : undefined,
    )
    navigate(link.to, { state: link.state })
  }
  const legend = data
    ? [...new Map(
        [...data.merchants]
          .sort((a, b) => Number(b.total) - Number(a.total))
          .map((m) => {
            const groupId = groupOf(m)
            return [groupId ?? '', groupId ? palette.groupName(groupId) : undefined] as const
          })
          .filter(([id]) => id),
      ).entries()].slice(0, 6)
    : []

  const common = { stale, onRetry: () => habits.refetch() }
  useReportedRange(data?.period)

  return (
    <div className="space-y-5" data-testid="habits-tab">
      <Headline text={headline} loading={!data && !habits.isError} stale={stale} />

      <ReportBlock
        {...common}
        title={t('reports.habits.bubbles.title')}
        description={bubblesAnswer}
        state={blockState(habits, (data?.merchants.length ?? 0) === 0)}
        emptyText={t('reports.habits.empty')}
        skeletonHeight={320}
        testId="block-bubbles"
      >
        {data && data.merchants.length > 0 && (
          <>
            {legend.length > 0 && (
              <ul className="mb-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground" aria-label={t('reports.habits.bubbles.legend')}>
                {legend.map(([id, name]) => (
                  <li key={id} className="flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: palette.group(id) }} />
                    {name}
                  </li>
                ))}
              </ul>
            )}
            <BubbleChart merchants={data.merchants} currency={currency} locale={locale} colorOf={colorOf} onOpen={openMerchant} ariaLabel={bubblesAnswer} />
          </>
        )}
      </ReportBlock>

      <div className="grid gap-5 lg:grid-cols-2">
        <ReportBlock
          {...common}
          title={t('reports.habits.weekdays.title')}
          description={weekdayAnswer}
          state={blockState(habits, (data?.weekdays.length ?? 0) === 0 || sum?.count === 0)}
          emptyText={t('reports.habits.empty')}
          testId="block-weekdays"
        >
          {data && <WeekdayBars weekdays={data.weekdays} currency={currency} locale={locale} />}
        </ReportBlock>
        <ReportBlock
          {...common}
          title={t('reports.habits.bands.title')}
          description={bandsAnswer}
          state={blockState(habits, (data?.bands.length ?? 0) === 0 || sum?.count === 0)}
          emptyText={t('reports.habits.empty')}
          testId="block-bands"
        >
          {data && <BandBars bands={data.bands} currency={currency} locale={locale} />}
        </ReportBlock>
      </div>

      <ReportBlock
        {...common}
        title={t('reports.habits.calendar.title')}
        description={calendarAnswer}
        state={blockState(habits, (data?.days.length ?? 0) === 0)}
        emptyText={t('reports.habits.empty')}
        skeletonHeight={140}
        testId="block-calendar"
      >
        {data && (
          <CalendarHeatmap from={data.period.from} to={data.period.to} days={data.days} currency={currency} locale={locale} ariaLabel={calendarAnswer} />
        )}
      </ReportBlock>
    </div>
  )
}
