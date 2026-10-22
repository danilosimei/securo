/** Reports > Explore: where the money went, one level at a time (group,
 *  category, store, then the store's transactions). The whole position lives
 *  in the URL (`?tab=explore&period=last_12m&group=…&category=…&merchant=…`,
 *  plus `from`/`to` for a custom range) so a link, a reload or the browser's
 *  Back lands on the same view. A period switch keeps the previous table up,
 *  dimmed, until the new one lands. */
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { useLocation, useSearchParams } from 'react-router-dom'
import { AlertCircle, ChevronRight } from 'lucide-react'
import { reports, type BreakdownParams, type BreakdownRow, type ReportPeriod } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { cn } from '@/lib/utils'
import { DEFAULT_REPORT_PERIOD, isReportPeriod, monthLabel } from '@/lib/report-period'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { Button } from '@/components/ui/button'
import { useReportedRange } from '@/components/reports/common/report-range'
import { Skeleton } from '@/components/ui/skeleton'
import { FadePanel } from '@/components/ui/fade-panel'
import { Sparkline } from '@/components/reports/explore/sparkline'
import { MerchantTransactions } from '@/components/reports/explore/merchant-transactions'
import { nextStep } from '@/components/reports/explore/drilldown'
import { ExploreTreemap } from '@/components/reports/explore/explore-treemap'
import { NEUTRAL_COLOR, useGroupPalette } from '@/components/reports/common/group-colors'

const DEFAULT_PERIOD: ReportPeriod = DEFAULT_REPORT_PERIOD

function isPeriod(value: string | null): value is ReportPeriod {
  return isReportPeriod(value)
}

function formatChange(change: number, locale: string) {
  return new Intl.NumberFormat(locale, {
    style: 'percent',
    maximumFractionDigits: 0,
    signDisplay: 'exceptZero',
  }).format(change)
}

/** More spending is the bad direction here. */
function changeTone(change: number) {
  if (change > 0) return 'text-rose-600 dark:text-rose-400'
  if (change < 0) return 'text-emerald-600 dark:text-emerald-400'
  return 'text-muted-foreground'
}

type Level = 'group' | 'category' | 'merchant'

/** Names of the levels already opened, carried in the navigation state so the
 *  breadcrumb can show them. A link opened cold has none: "…" stands in. */
interface Labels {
  group?: string
  category?: string
  merchant?: string
}

export function ExploreTab() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const palette = useGroupPalette()
  const [searchParams, setSearchParams] = useSearchParams()
  const location = useLocation()
  const stateLabels: Labels = (location.state as { exploreLabels?: Labels } | null)?.exploreLabels ?? {}

  const group = searchParams.get('group')
  const category = searchParams.get('category')
  const merchant = category ? searchParams.get('merchant') : null
  const level: Level = category ? 'merchant' : group ? 'category' : 'group'

  const requested = searchParams.get('period')
  const from = searchParams.get('from') ?? ''
  const to = searchParams.get('to') ?? ''
  const hasCustom = !!from && !!to
  const requestedPeriod = isPeriod(requested) ? requested : DEFAULT_PERIOD
  // A custom period without both dates cannot be asked for: fall back.
  const period: ReportPeriod = requestedPeriod === 'custom' && !hasCustom ? DEFAULT_PERIOD : requestedPeriod

  // Going down (or up) pushes a history entry, so Back climbs one level.
  const go = (patch: Record<string, string | null>, labels: Labels) =>
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev)
        for (const [key, value] of Object.entries(patch)) {
          if (value === null) params.delete(key)
          else params.set(key, value)
        }
        return params
      },
      { state: { exploreLabels: labels } },
    )

  const params: BreakdownParams = {
    level,
    ...(level === 'category' && group ? { group_id: group } : {}),
    ...(level === 'merchant' && category ? { category_id: category } : {}),
    period,
    ...(period === 'custom' ? { from, to } : {}),
  }
  const query = useQuery({
    queryKey: ['reports', 'breakdown', params],
    queryFn: () => reports.breakdown(params),
    // Only a period switch keeps the old table; another level is another table.
    placeholderData: (previous, previousQuery) => {
      const before = previousQuery?.queryKey[2] as BreakdownParams | undefined
      return before?.level === params.level &&
        before.group_id === params.group_id &&
        before.category_id === params.category_id
        ? previous
        : undefined
    },
  })
  const data = query.data
  useReportedRange(data?.period)
  const stale = query.isPlaceholderData

  const open = (row: BreakdownRow) => {
    const step = nextStep(level, row)
    if (!step) return
    if ('group' in step) go({ group: step.group, category: null, merchant: null }, { group: row.label })
    else if ('category' in step) {
      go({ category: step.category, merchant: null }, { group: stateLabels.group, category: row.label })
    } else go({ merchant: step.merchant }, { ...stateLabels, merchant: row.label })
  }

  const merchantRow = merchant ? data?.rows.find((r) => r.id === merchant) : undefined
  const merchantLabel = merchantRow?.label ?? stateLabels.merchant ?? '…'
  const crumbs: { key: string; label: string; onClick?: () => void }[] = [
    {
      key: 'root',
      label: t('reports.explore.root'),
      onClick: () => go({ group: null, category: null, merchant: null }, {}),
    },
  ]
  if (group) {
    crumbs.push({
      key: 'group',
      label: stateLabels.group ?? '…',
      onClick: level === 'category' ? undefined : () => go({ category: null, merchant: null }, { group: stateLabels.group }),
    })
  }
  if (category) {
    crumbs.push({
      key: 'category',
      label: stateLabels.category ?? '…',
      onClick: merchant ? () => go({ merchant: null }, { ...stateLabels, merchant: undefined }) : undefined,
    })
  }
  if (merchant) crumbs.push({ key: 'merchant', label: merchantLabel })

  // Tiles wear their group's color; below the group level, a shade of it.
  const groupColor = group && group !== 'none' ? palette.group(group) : NEUTRAL_COLOR
  const colorOf = (row: BreakdownRow) => {
    if (level === 'group') return palette.group(row.id, row.label)
    if (level === 'category') {
      const own = palette.category(row.id, row.label)
      return own === NEUTRAL_COLOR ? groupColor : own
    }
    const own = category && category !== 'none' ? palette.category(category) : NEUTRAL_COLOR
    return own === NEUTRAL_COLOR ? groupColor : own
  }

  const total = data ? Number(data.total) : 0
  const previous = data?.previous_total != null ? Number(data.previous_total) : 0
  const headerChange = data && previous > 0 ? (total - previous) / previous : null

  return (
    <div className="space-y-4">
      {level !== 'group' && (
        <nav aria-label={t('reports.explore.breadcrumb')}>
          <ol className="flex flex-wrap items-center gap-1 text-sm">
            {crumbs.map((crumb, i) => (
              <li key={crumb.key} className="flex items-center gap-1">
                {i > 0 && <ChevronRight size={14} className="text-muted-foreground" aria-hidden="true" />}
                {crumb.onClick ? (
                  <button
                    type="button"
                    onClick={crumb.onClick}
                    className="rounded text-muted-foreground hover:text-foreground hover:underline"
                  >
                    {crumb.label}
                  </button>
                ) : (
                  <span aria-current="page" className="font-medium text-foreground">
                    {crumb.label}
                  </span>
                )}
              </li>
            ))}
          </ol>
        </nav>
      )}

      {query.isError ? (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-2.5 dark:border-rose-500/30 dark:bg-rose-500/10"
        >
          <div className="flex min-w-0 items-center gap-2.5">
            <AlertCircle size={16} className="shrink-0 text-rose-600 dark:text-rose-400" />
            <span className="text-sm text-rose-900 dark:text-rose-200">{t('reports.explore.error')}</span>
          </div>
          <Button type="button" variant="ghost" size="sm" onClick={() => query.refetch()}>
            {t('reports.explore.retry')}
          </Button>
        </div>
      ) : !data ? (
        <div className="space-y-3" aria-busy="true" data-testid="explore-skeleton">
          <Skeleton className="h-14 w-56" />
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
      ) : (
        <FadePanel panelKey={`${level}:${group ?? ''}:${category ?? ''}`} className="space-y-4">
        <div
          aria-busy={stale}
          data-testid="explore-content"
          className={cn('space-y-4 transition-opacity duration-200', stale && 'opacity-60')}
        >
          <div className="rounded-xl border border-border bg-card px-5 py-4 shadow-sm">
            <p className="mb-0.5 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t('reports.explore.spending')}
            </p>
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <p className="text-3xl font-bold tabular-nums text-foreground">
                {mask(formatCurrency(total, data.currency, locale))}
              </p>
              {headerChange !== null && (
                <span className={cn('text-sm font-semibold tabular-nums', changeTone(headerChange))}>
                  {t('reports.explore.vsPrevious', { change: formatChange(headerChange, locale) })}
                </span>
              )}
            </div>
          </div>

          {data.unconverted_count > 0 && (
            <p className="text-xs text-amber-700 dark:text-amber-400">
              {t('reports.explore.unconverted', { n: data.unconverted_count })}
            </p>
          )}

          {data.rows.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              {t('reports.explore.empty', {
                from: monthLabel(data.period.from, locale),
                to: monthLabel(data.period.to, locale),
              })}
            </p>
          ) : (
            <>
            {data.rows.some((r) => Number(r.total) > 0) && (
            <div className="rounded-xl border border-border bg-card p-2 shadow-sm">
              <ExploreTreemap
                rows={data.rows}
                level={level}
                currency={data.currency}
                locale={locale}
                colorOf={colorOf}
                onOpen={open}
                selected={merchant}
              />
            </div>
            )}
            <div className="rounded-xl border border-border bg-card shadow-sm">
              <table className="w-full table-fixed text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs font-medium text-muted-foreground">
                    <th className="px-3 py-2.5 sm:px-4">{t(`reports.explore.col.${level}`)}</th>
                    <th className="w-24 px-2 py-2.5 text-right sm:w-32">{t('reports.explore.col.total')}</th>
                    <th className="w-14 px-2 py-2.5 text-right sm:w-16">{t('reports.explore.col.share')}</th>
                    <th className="w-16 px-2 py-2.5 text-right sm:w-20">{t('reports.explore.col.change')}</th>
                    <th className="hidden w-24 px-2 py-2.5 text-right sm:table-cell">{t('reports.explore.col.count')}</th>
                    <th className="hidden w-24 px-3 py-2.5 sm:table-cell sm:px-4">{t('reports.explore.col.trend')}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row) => {
                    const value = Number(row.total)
                    const offsets = value < 0
                    return (
                      <tr
                        key={row.id ?? row.label}
                        tabIndex={0}
                        data-selected={merchant !== null && row.id === merchant}
                        onClick={() => open(row)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') open(row)
                        }}
                        className={cn(
                          'cursor-pointer border-b border-border last:border-b-0 hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none',
                          merchant !== null && row.id === merchant && 'bg-primary/5',
                        )}
                      >
                        <td className="truncate px-3 py-2.5 font-medium text-foreground sm:px-4">{row.label}</td>
                        <td className="px-2 py-2.5 text-right tabular-nums">
                          {mask(formatCurrency(value, data.currency, locale))}
                        </td>
                        <td className="px-2 py-2.5 text-right tabular-nums text-muted-foreground">
                          {offsets ? t('reports.explore.offsets') : `${Math.round(row.share * 100)}%`}
                        </td>
                        <td
                          className={cn(
                            'px-2 py-2.5 text-right tabular-nums',
                            row.change === null ? 'text-muted-foreground' : changeTone(row.change),
                          )}
                        >
                          {row.change === null ? '—' : formatChange(row.change, locale)}
                        </td>
                        <td className="hidden px-2 py-2.5 text-right tabular-nums text-muted-foreground sm:table-cell">
                          {row.count}
                        </td>
                        <td className="hidden px-3 py-2.5 text-primary sm:table-cell sm:px-4">
                          <Sparkline values={row.sparkline} />
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            </>
          )}
        </div>
        {merchant && (
          <MerchantTransactions
            key={`${category}:${merchant}:${data.period.from}:${data.period.to}`}
            merchantLabel={merchantLabel}
            merchantKey={merchant}
            categoryId={category!}
            range={{ from: data.period.from, to: data.period.to }}
          />
        )}
        </FadePanel>
      )}
    </div>
  )
}
