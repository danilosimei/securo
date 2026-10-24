/** "Comparar": the spending of the period (A) next to another period (B),
 *  group by group, with the difference written out ("R$ 1,2 mil a mais") and
 *  a small bar to the right (more) or to the left (less). B is the period
 *  right before, the same dates a year earlier, or dates of choice. A group
 *  opens its categories, compared the same way. */
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, Scale } from 'lucide-react'
import { reports, type BreakdownCompare, type BreakdownParams, type BreakdownRow, type ReportPeriodParams } from '@/lib/api'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { Segmented } from '@/components/invoice-ui'
import { DateRangePicker } from '@/components/ui/date-range-picker'
import { Skeleton } from '@/components/ui/skeleton'
import { FadePanel } from '@/components/ui/fade-panel'
import { BlockError } from '@/components/reports/common/report-block'
import { AnswerLine } from '@/components/reports/common/answer-line'
import { answerParts } from '@/components/reports/common/answer-parts'
import { formatMoneyShort } from '@/components/reports/common/chart-utils'
import { rangeLabel } from './explore-model'
import type { GroupPalette } from '@/components/reports/common/group-colors'
import { CUSTOM_RANGE_MAX_YEARS } from '@/components/reports/period-selector'

interface CompareViewProps {
  query: ReportPeriodParams
  mode: BreakdownCompare
  from: string
  to: string
  group: string | null
  groupLabel?: string
  palette: GroupPalette
  locale: string
  onMode: (mode: BreakdownCompare, range?: { from: string; to: string }) => void
  onOpenGroup: (row: BreakdownRow) => void
  onBack: () => void
}

export function CompareView({ query, mode, from, to, group, groupLabel, palette, locale, onMode, onOpenGroup, onBack }: CompareViewProps) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const ready = mode !== 'custom' || (!!from && !!to)
  const params: BreakdownParams = {
    ...query,
    level: group ? 'category' : 'group',
    ...(group ? { group_id: group } : {}),
    compare: mode,
    ...(mode === 'custom' ? { compare_from: from, compare_to: to } : {}),
  }
  const result = useQuery({
    queryKey: ['reports', 'breakdown', params],
    queryFn: () => reports.breakdown(params),
    enabled: ready,
    placeholderData: (previous) => previous,
  })
  const data = result.data
  const stale = result.isPlaceholderData
  const currency = data?.currency ?? 'BRL'
  const money = (v: number) => mask(formatMoneyShort(Math.abs(v), currency, locale))

  const total = data ? Number(data.total) : 0
  const before = data?.previous_total ? Number(data.previous_total) : 0
  const labelA = data ? rangeLabel(data.period.from, data.period.to, locale) : ''
  const labelB = data ? rangeLabel(data.period.previous_from, data.period.previous_to, locale) : ''
  const diff = total - before
  const answer = data
    ? answerParts(t, diff > 0 ? 'reports.explore.compare.answerMore' : diff < 0 ? 'reports.explore.compare.answerLess' : 'reports.explore.compare.answerSame', {
        value: { kind: 'value', text: money(diff) },
        change: { kind: 'delta', change: before > 0 ? diff / before : 0, good: diff < 0, locale },
        b: labelB,
      })
    : null

  // Groups (or categories) present in A or in B, biggest A first.
  const rows = (data?.rows ?? []).filter((r) => Number(r.total) !== 0 || Number(r.previous_total) !== 0)
  const maxAbs = Math.max(...rows.map((r) => Math.abs(Number(r.total) - Number(r.previous_total))), 1)
  const colorOf = (row: BreakdownRow) => (group ? palette.category(row.id, row.label) : palette.group(row.id, row.label))

  return (
    <div className="space-y-4" data-testid="compare-view">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">{t('reports.explore.compare.with')}</span>
        <div className="max-w-full overflow-x-auto">
          <Segmented
            value={mode}
            onChange={(next) => onMode(next)}
            testIdPrefix="compare-mode"
            options={[
              { value: 'previous', label: t('reports.explore.compare.previous') },
              { value: 'year_ago', label: t('reports.explore.compare.yearAgo') },
              { value: 'custom', label: t('reports.explore.compare.custom') },
            ]}
          />
        </div>
        {mode === 'custom' && (
          <DateRangePicker
            from={from}
            to={to}
            onChange={(f, tt) => (f && tt ? onMode('custom', { from: f, to: tt }) : undefined)}
            disallowFuture
            maxRangeYears={CUSTOM_RANGE_MAX_YEARS}
            size="sm"
            label={t('reports.explore.compare.pick')}
            placeholder={t('reports.explore.compare.pick')}
          />
        )}
      </div>

      {group && (
        <nav aria-label={t('reports.explore.breadcrumb')} className="flex items-center gap-1 text-sm">
          <button type="button" onClick={onBack} className="rounded text-muted-foreground hover:text-foreground hover:underline">
            {t('reports.explore.compare.allGroups')}
          </button>
          <ChevronRight size={14} className="text-muted-foreground" aria-hidden="true" />
          <span aria-current="page" className="font-medium text-foreground">{groupLabel ?? '…'}</span>
        </nav>
      )}

      {!ready ? (
        <p className="rounded-xl border border-dashed border-border py-10 text-center text-sm text-muted-foreground">
          {t('reports.explore.compare.pickHint')}
        </p>
      ) : result.isError && !data ? (
        <BlockError onRetry={() => result.refetch()} message={t('reports.explore.error')} />
      ) : !data ? (
        <div className="space-y-3" aria-busy="true">
          <Skeleton className="h-16 w-full" />
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : (
        <FadePanel panelKey={`${group ?? ''}:${mode}`} className={cn('space-y-4 transition-opacity', stale && 'opacity-60')} aria-busy={stale || undefined}>
          <div className="rounded-xl border border-border bg-card p-4 shadow-sm sm:p-5">
            <AnswerLine parts={answer} icon={Scale} />
            <dl className="mt-4 grid grid-cols-2 gap-3 sm:max-w-md">
              <div className="rounded-lg bg-muted/50 px-3 py-2">
                <dt className="text-[11px] font-medium text-muted-foreground">{t('reports.explore.compare.a', { range: labelA })}</dt>
                <dd className="text-lg font-semibold text-foreground">{mask(formatMoneyShort(total, currency, locale))}</dd>
              </div>
              <div className="rounded-lg bg-muted/50 px-3 py-2">
                <dt className="text-[11px] font-medium text-muted-foreground">{t('reports.explore.compare.b', { range: labelB })}</dt>
                <dd className="text-lg font-semibold text-foreground">{mask(formatMoneyShort(before, currency, locale))}</dd>
              </div>
            </dl>
          </div>

          {rows.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">{t('reports.explore.compare.empty')}</p>
          ) : (
            <div className="rounded-xl border border-border bg-card shadow-sm">
              <table className="w-full table-fixed text-sm" data-testid="compare-table">
                <thead>
                  <tr className="border-b border-border text-left text-xs font-medium text-muted-foreground">
                    <th className="px-3 py-2.5 sm:px-4">{t(group ? 'reports.explore.col.category' : 'reports.explore.col.group')}</th>
                    <th className="hidden w-32 px-2 py-2.5 text-right sm:table-cell" title={labelA}>A</th>
                    <th className="hidden w-32 px-2 py-2.5 text-right sm:table-cell" title={labelB}>B</th>
                    <th className="w-36 px-3 py-2.5 sm:w-52 sm:px-4">{t('reports.explore.compare.difference')}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const a = Number(row.total)
                    const b = Number(row.previous_total)
                    const d = a - b
                    const canOpen = !group && row.kind === 'group' && row.id !== null
                    const diffText =
                      d === 0
                        ? t('reports.explore.compare.same')
                        : t(d > 0 ? 'reports.explore.compare.more' : 'reports.explore.compare.less', { value: money(d) })
                    return (
                      <tr
                        key={row.id ?? row.label}
                        data-testid="compare-row"
                        tabIndex={canOpen ? 0 : undefined}
                        onClick={canOpen ? () => onOpenGroup(row) : undefined}
                        onKeyDown={canOpen ? (e) => { if (e.key === 'Enter') onOpenGroup(row) } : undefined}
                        className={cn(
                          'border-b border-border last:border-b-0',
                          canOpen && 'cursor-pointer hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none',
                        )}
                      >
                        <td className="px-3 py-2.5 sm:px-4">
                          <span className="flex min-w-0 items-center gap-2">
                            <span aria-hidden="true" className="size-2.5 shrink-0 rounded-sm" style={{ background: colorOf(row) }} />
                            <span className="truncate font-medium text-foreground">{row.label}</span>
                          </span>
                          <span className="mt-0.5 block truncate pl-[18px] text-[11px] tabular-nums text-muted-foreground sm:hidden">
                            {mask(formatMoneyShort(a, currency, locale))} · {mask(formatMoneyShort(b, currency, locale))}
                          </span>
                        </td>
                        <td className="hidden px-2 py-2.5 text-right tabular-nums sm:table-cell">{mask(formatCurrency(a, currency, locale))}</td>
                        <td className="hidden px-2 py-2.5 text-right tabular-nums text-muted-foreground sm:table-cell">{mask(formatCurrency(b, currency, locale))}</td>
                        <td className="px-3 py-2.5 sm:px-4">
                          <span
                            className={cn(
                              'flex items-center gap-1 text-xs font-semibold tabular-nums',
                              d > 0 ? 'text-rose-700 dark:text-rose-300' : d < 0 ? 'text-emerald-700 dark:text-emerald-300' : 'text-muted-foreground',
                            )}
                          >
                            {d !== 0 && <span aria-hidden="true">{d > 0 ? '▲' : '▼'}</span>}
                            {diffText}
                          </span>
                          {/* Right of the middle = more than in B, left = less. */}
                          <span aria-hidden="true" className="relative mt-1.5 block h-1.5 w-full">
                            <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
                            {d !== 0 && (
                              <span
                                className={cn('absolute inset-y-0 rounded-full', d > 0 ? 'bg-rose-500 dark:bg-rose-400' : 'bg-emerald-500 dark:bg-emerald-400')}
                                style={
                                  d > 0
                                    ? { left: '50%', width: `${(Math.abs(d) / maxAbs) * 50}%` }
                                    : { right: '50%', width: `${(Math.abs(d) / maxAbs) * 50}%` }
                                }
                              />
                            )}
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </FadePanel>
      )}
    </div>
  )
}
