/** Reports > Tendências: what is growing. A headline names the category that
 *  rose most in the last 3 months; below, the spending of the 12 months up to
 *  the period's end by group, and a mini chart per category. */
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { reports, type BreakdownParams } from '@/lib/api'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useReportedRange } from '@/components/reports/common/report-range'
import { Headline, ReportBlock } from '@/components/reports/common/report-block'
import { blockState } from '@/components/reports/common/block-state'
import { formatMoneyShort, formatPercent, monthName, sparklineMonths } from '@/components/reports/common/chart-utils'
import { NEUTRAL_COLOR, useGroupPalette } from '@/components/reports/common/group-colors'
import { exploreLink, useReportPeriod } from '@/components/reports/common/report-url'
import { StackedArea } from './stacked-area'
import { CategoryGrid } from './category-grid'
import { buildLayers, categoryTrends, trendExtremes, type CategoryTrend, type Layer } from './trends-model'

export function TrendsTab() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const navigate = useNavigate()
  const palette = useGroupPalette()
  const { query } = useReportPeriod()

  const groupParams: BreakdownParams = { level: 'group', ...query }
  const categoryParams: BreakdownParams = { level: 'category', group_id: 'all', ...query }
  const groups = useQuery({
    queryKey: ['reports', 'breakdown', groupParams],
    queryFn: () => reports.breakdown(groupParams),
    placeholderData: keepPreviousData,
  })
  const categories = useQuery({
    queryKey: ['reports', 'breakdown', categoryParams],
    queryFn: () => reports.breakdown(categoryParams),
    placeholderData: keepPreviousData,
  })

  const data = groups.data
  const currency = data?.currency ?? categories.data?.currency ?? 'BRL'
  const money = (v: number) => mask(formatMoneyShort(v, currency, locale))
  const months = data ? sparklineMonths(data.period.to) : []
  const layers = useMemo(() => (data ? buildLayers(data.rows, t('reports.trends.restGroups')) : []), [data, t])
  const trends = useMemo(() => (categories.data ? categoryTrends(categories.data.rows) : []), [categories.data])
  const { up, down } = trendExtremes(trends)

  const headline = !categories.data
    ? null
    : up
      ? t('reports.trends.headlineUp', {
          name: up.row.label,
          change: formatPercent(up.change ?? 0, locale, true),
          value: money(up.perMonth),
        })
      : down
        ? t('reports.trends.headlineDown', { name: down.row.label, change: formatPercent(down.change ?? 0, locale, true) })
        : t('reports.trends.headlineFlat')

  const lastMonth = months.length - 1
  const lastTotal = layers.reduce((s, l) => s + (l.values[lastMonth] ?? 0), 0)
  const areaAnswer = layers[0] && months.length
    ? t('reports.trends.areaAnswer', {
        month: monthName(months[lastMonth], locale),
        total: money(lastTotal),
        group: layers[0].label,
      })
    : ''

  const layerColor = (layer: Layer) => (layer.rest ? NEUTRAL_COLOR : palette.group(layer.id, layer.label))
  const trendColor = (tr: CategoryTrend) => {
    const own = palette.category(tr.row.id, tr.row.label)
    return own === NEUTRAL_COLOR ? palette.group(null, tr.row.group_label ?? undefined) : own
  }
  const openCategory = (tr: CategoryTrend) => {
    const group = tr.row.id ? palette.groupOfCategory(tr.row.id) : null
    const link = exploreLink(
      { ...query, group, category: tr.row.id ?? 'none' },
      { group: tr.row.group_label ?? undefined, category: tr.row.label },
    )
    navigate(link.to, { state: link.state })
  }

  useReportedRange(data?.period)

  return (
    <div className="space-y-5" data-testid="trends-tab">
      <Headline text={headline} loading={!categories.data && !categories.isError} stale={categories.isPlaceholderData} />

      <ReportBlock
        title={t('reports.trends.areaTitle')}
        description={areaAnswer}
        state={blockState(groups, layers.length === 0)}
        stale={groups.isPlaceholderData}
        onRetry={() => groups.refetch()}
        emptyText={t('reports.trends.empty')}
        skeletonHeight={300}
        testId="block-trends-area"
      >
        {data && (
          <>
            <p className="mb-2 text-[11px] text-muted-foreground">
              {t('reports.trends.window', { from: monthName(months[0], locale, 'short'), to: monthName(months[lastMonth], locale, 'short') })}
            </p>
            <StackedArea layers={layers} months={months} colorOf={layerColor} currency={currency} locale={locale} ariaLabel={areaAnswer} />
          </>
        )}
      </ReportBlock>

      <ReportBlock
        title={t('reports.trends.gridTitle')}
        description={t('reports.trends.gridDescription')}
        state={blockState(categories, trends.length === 0)}
        stale={categories.isPlaceholderData}
        onRetry={() => categories.refetch()}
        emptyText={t('reports.trends.empty')}
        skeletonHeight={260}
        testId="block-trends-grid"
      >
        <CategoryGrid trends={trends} currency={currency} locale={locale} colorOf={trendColor} onOpen={openCategory} />
      </ReportBlock>
    </div>
  )
}
