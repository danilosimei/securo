/** Reports > Explore: where the money went, one level at a time. This slice
 *  covers the top level (groups). The period lives in the URL
 *  (`?tab=explore&period=last_12m`, plus `from`/`to` for a custom range) so a
 *  link or a reload lands on the same view. A period switch keeps the previous
 *  table up, dimmed, until the new one lands. */
import { useTranslation } from 'react-i18next'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { AlertCircle } from 'lucide-react'
import { reports, type BreakdownParams, type BreakdownRow, type ReportPeriod } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { localDateString } from '@/lib/date-utils'
import { cn } from '@/lib/utils'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { Button } from '@/components/ui/button'
import { DateRangePicker } from '@/components/ui/date-range-picker'
import { Skeleton } from '@/components/ui/skeleton'
import { Sparkline } from '@/components/reports/explore/sparkline'

const PERIODS: ReportPeriod[] = ['this_month', 'last_month', 'last_3m', 'last_6m', 'last_12m', 'ytd', 'custom']
const DEFAULT_PERIOD: ReportPeriod = 'last_12m'
const CUSTOM_RANGE_MAX_YEARS = 10

function isPeriod(value: string | null): value is ReportPeriod {
  return PERIODS.includes(value as ReportPeriod)
}

function monthLabel(iso: string, locale: string) {
  return new Date(iso + 'T00:00:00').toLocaleDateString(locale, { month: 'short', year: '2-digit' })
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

interface ExploreTabProps {
  /** Reserved for drilling down a level; rows are not clickable without it. */
  onSelectRow?: (row: BreakdownRow) => void
}

export function ExploreTab({ onSelectRow }: ExploreTabProps = {}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const [searchParams, setSearchParams] = useSearchParams()

  const requested = searchParams.get('period')
  const from = searchParams.get('from') ?? ''
  const to = searchParams.get('to') ?? ''
  const hasCustom = !!from && !!to
  const requestedPeriod = isPeriod(requested) ? requested : DEFAULT_PERIOD
  // A custom period without both dates cannot be asked for: fall back.
  const period: ReportPeriod = requestedPeriod === 'custom' && !hasCustom ? DEFAULT_PERIOD : requestedPeriod

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
      { replace: true },
    )

  const params: BreakdownParams = {
    level: 'group',
    period,
    ...(period === 'custom' ? { from, to } : {}),
  }
  const query = useQuery({
    queryKey: ['reports', 'breakdown', params],
    queryFn: () => reports.breakdown(params),
    placeholderData: keepPreviousData,
  })
  const data = query.data
  const stale = query.isPlaceholderData

  const today = localDateString()
  const customDefaults = { from: `${today.slice(0, 4)}-01-01`, to: today }

  const rangeText = data
    ? t(data.period.partial ? 'reports.explore.rangePartial' : 'reports.explore.rangeClosed', {
        from: monthLabel(data.period.from, locale),
        to: monthLabel(data.period.to, locale),
      })
    : null

  const total = data ? Number(data.total) : 0
  const previous = data?.previous_total != null ? Number(data.previous_total) : 0
  const headerChange = data && previous > 0 ? (total - previous) / previous : null

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <div
          role="group"
          aria-label={t('reports.explore.tab')}
          className="inline-flex max-w-full flex-wrap items-center gap-0.5 rounded-lg border border-border bg-muted p-0.5 text-xs font-medium"
        >
          {PERIODS.filter((p) => p !== 'custom').map((p) => (
            <button
              key={p}
              type="button"
              aria-pressed={period === p}
              onClick={() => update(p)}
              className={cn(
                'rounded-md px-3 py-1.5 whitespace-nowrap transition-colors',
                period === p
                  ? 'bg-card text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {t(`reports.explore.period.${p}`)}
            </button>
          ))}
          <DateRangePicker
            variant="segment"
            className="rounded-md"
            active={period === 'custom'}
            from={from}
            to={to}
            defaultFrom={customDefaults.from}
            defaultTo={customDefaults.to}
            disallowFuture
            maxRangeYears={CUSTOM_RANGE_MAX_YEARS}
            onChange={(f, e) => (f && e ? update('custom', { from: f, to: e }) : update(DEFAULT_PERIOD))}
            label={t('reports.explore.period.custom')}
            placeholder={t('reports.explore.period.custom')}
          />
        </div>
        {rangeText && <p className="text-xs text-muted-foreground">{rangeText}</p>}
      </div>

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
            <div className="rounded-xl border border-border bg-card shadow-sm">
              <table className="w-full table-fixed text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs font-medium text-muted-foreground">
                    <th className="px-3 py-2.5 sm:px-4">{t('reports.explore.col.group')}</th>
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
                        onClick={onSelectRow ? () => onSelectRow(row) : undefined}
                        className="border-b border-border last:border-b-0"
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
          )}
        </div>
      )}
    </div>
  )
}
