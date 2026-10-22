/** Reports > Visão geral: the answer first (one sentence), then the four
 *  numbers, the highlights, what changed, month by month and the money map.
 *  The period lives in the URL; switching it keeps the previous numbers up,
 *  dimmed, until the new ones land. */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { reports, type OverviewResponse } from '@/lib/api'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { Segmented } from '@/components/invoice-ui'
import { ReportPeriodSelector } from '@/components/reports/period-selector'
import { BlockError, Headline, ReportBlock } from '@/components/reports/common/report-block'
import { blockState } from '@/components/reports/common/block-state'
import { formatMoneyShort, monthName } from '@/components/reports/common/chart-utils'
import { useGroupPalette } from '@/components/reports/common/group-colors'
import { tabLink, useReportPeriod } from '@/components/reports/common/report-url'
import { MoneyMapView } from '@/components/reports/money-map/money-map'
import { overviewHeadline } from './headline'
import { KpiRow } from './kpi-row'
import { Highlights } from './highlights'
import { ChangesChart } from './changes-chart'
import { MonthlyChart } from './monthly-chart'
import { changesSummary, peakMonth } from './overview-model'

export function OverviewTab() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const palette = useGroupPalette()
  const { period, from, to, query, update } = useReportPeriod()
  const [mapView, setMapView] = useState<'chart' | 'table'>('chart')

  const overview = useQuery({
    queryKey: ['reports', 'overview', query],
    queryFn: () => reports.overview(query),
    placeholderData: keepPreviousData,
  })
  const data = overview.data
  const stale = overview.isPlaceholderData
  // The same report over the previous period feeds the "vs before" figures.
  const previousQuery = data && !data.period.partial
    ? { period: 'custom' as const, from: data.period.previous_from, to: data.period.previous_to }
    : null
  const previous = useQuery({
    queryKey: ['reports', 'overview', previousQuery],
    queryFn: () => reports.overview(previousQuery!),
    enabled: previousQuery !== null && !stale,
    placeholderData: keepPreviousData,
  })
  const prev: OverviewResponse | null = previousQuery && !stale ? (previous.data ?? null) : null

  const currency = data?.currency ?? user?.preferences?.currency_display ?? 'BRL'
  const money = (v: number) => mask(formatMoneyShort(v, currency, locale))
  const headline = data ? overviewHeadline({ data, previous: prev, currency, locale, t, mask }) : null
  const isEmpty = !!data && Number(data.kpis.spending) === 0 && Number(data.kpis.income) === 0

  const changes = data ? changesSummary(data.changes) : null
  const changesAnswer = changes?.up
    ? t('reports.overview.changes.answerUp', { name: changes.up.label, value: money(Number(changes.up.delta)) })
    : changes?.down
      ? t('reports.overview.changes.answerDown', { name: changes.down.label, value: money(-Number(changes.down.delta)) })
      : undefined
  const peak = data ? peakMonth(data.monthly) : null
  const monthlyAnswer = peak
    ? t(peak.spike ? 'reports.overview.monthly.ariaSpike' : 'reports.overview.monthly.aria', {
        month: monthName(peak.month.month, locale),
        value: money(Number(peak.month.spending)),
        multiple: peak.multiple?.toLocaleString(locale, { maximumFractionDigits: 1 }) ?? '',
        median: money(Number(peak.month.median_spending)),
      })
    : undefined

  return (
    <div className="space-y-5" data-testid="overview-tab">
      <ReportPeriodSelector
        period={period}
        from={from}
        to={to}
        onChange={update}
        locale={locale}
        range={data ? { from: data.period.from, to: data.period.to, partial: data.period.partial } : null}
      />

      {overview.isError && !data ? (
        <BlockError onRetry={() => overview.refetch()} message={t('reports.overview.error')} />
      ) : isEmpty && !stale ? (
        <p className="rounded-xl border border-dashed border-border py-12 text-center text-sm text-muted-foreground">
          {t('reports.overview.empty')}
        </p>
      ) : (
        <>
          <Headline text={headline} loading={!data} stale={stale} />

          <KpiRow
            data={data}
            previous={prev}
            currency={currency}
            locale={locale}
            stale={stale}
            fixedHref={tabLink('recurring', query).to}
          />

          <ReportBlock
            title={t('reports.overview.highlights.title')}
            description={t('reports.overview.highlights.description')}
            state={blockState(overview, false)}
            stale={stale}
            onRetry={() => overview.refetch()}
            skeletonHeight={140}
            className="border-none bg-transparent shadow-none"
            bodyClassName="px-0 sm:px-0"
          >
            {data && <Highlights data={data} query={query} currency={currency} locale={locale} palette={palette} />}
          </ReportBlock>

          <div className="grid gap-5 lg:grid-cols-5">
            <ReportBlock
              title={t('reports.overview.monthly.title')}
              description={monthlyAnswer}
              state={blockState(overview, (data?.monthly.length ?? 0) < 2)}
              emptyText={t('reports.overview.monthly.tooShort')}
              stale={stale}
              onRetry={() => overview.refetch()}
              className="lg:col-span-3"
              testId="block-monthly"
            >
              {data && <MonthlyChart months={data.monthly} currency={currency} locale={locale} />}
            </ReportBlock>
            <ReportBlock
              title={t('reports.overview.changes.title')}
              description={changesAnswer}
              state={blockState(overview, (data?.changes.length ?? 0) === 0)}
              emptyText={t('reports.overview.changes.empty')}
              stale={stale}
              onRetry={() => overview.refetch()}
              className="lg:col-span-2"
              testId="block-changes"
            >
              {data && (
                <>
                  <p className="mb-2 text-[11px] text-muted-foreground">{t('reports.overview.changes.description')}</p>
                  <ChangesChart changes={data.changes} query={query} currency={currency} locale={locale} palette={palette} />
                </>
              )}
            </ReportBlock>
          </div>

          <ReportBlock
            title={t('reports.moneyMap.title')}
            state={blockState(overview, false)}
            stale={stale}
            onRetry={() => overview.refetch()}
            skeletonHeight={380}
            testId="block-money-map"
            action={
              <Segmented
                value={mapView}
                onChange={setMapView}
                testIdPrefix="money-map-view"
                options={[
                  { value: 'chart', label: t('reports.moneyMap.viewChart') },
                  { value: 'table', label: t('reports.moneyMap.viewTable') },
                ]}
              />
            }
          >
            {data && (
              <MoneyMapView
                map={data.money_map}
                query={query}
                currency={currency}
                locale={locale}
                palette={palette}
                view={mapView}
              />
            )}
          </ReportBlock>
        </>
      )}
    </div>
  )
}
