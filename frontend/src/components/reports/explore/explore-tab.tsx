/** Reports > Explore: where the money went. "Ver por" picks the angle: by
 *  category (group, then category, store and the store's transactions), by
 *  store, by month (a table of colors), by account, or two periods side by
 *  side. "Exibir como" draws a ranking as bars, blocks or lines; the table
 *  is always below. Everything lives in the URL (`view`, `chart`, `group`,
 *  `category`, `merchant`, `account`, `cmp`...), so a link, a reload or the
 *  browser's Back lands on the same view. A period switch keeps the previous
 *  render up, dimmed, until the new one lands. */
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { useLocation, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft,
  BarChart3,
  CalendarRange,
  ChevronRight,
  CreditCard,
  LayoutGrid,
  LineChart,
  Scale,
  Store,
  Tags,
  X,
  type LucideIcon,
} from 'lucide-react'
import { reports, type BreakdownCompare, type BreakdownParams, type BreakdownRow, type BreakdownResponse } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { cn } from '@/lib/utils'
import { monthLabel } from '@/lib/report-period'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { FadePanel } from '@/components/ui/fade-panel'
import { CategoryIcon } from '@/components/category-icon'
import { useReportedRange } from '@/components/reports/common/report-range'
import { BlockError } from '@/components/reports/common/report-block'
import { DeltaPill } from '@/components/reports/common/answer-line'
import { formatPercent, sparklineMonths } from '@/components/reports/common/chart-utils'
import { NEUTRAL_COLOR, useGroupPalette } from '@/components/reports/common/group-colors'
import { useReportPeriod } from '@/components/reports/common/report-url'
import { Sparkline } from './sparkline'
import { MerchantTransactions } from './merchant-transactions'
import { nextStep } from './drilldown'
import { ExploreTreemap } from './explore-treemap'
import { ExploreBars } from './explore-bars'
import { ExploreTrend } from './explore-trend'
import { MonthMatrix } from './month-matrix'
import { CompareView } from './compare-view'
import {
  RANKING_VIEWS,
  accountColors,
  concentration,
  monthRange,
  parseChart,
  parseCompare,
  parseView,
  trendSeries,
  type ExploreChart,
  type ExploreView,
  type TrendSeries,
} from './explore-model'

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

type Level = 'group' | 'category' | 'merchant' | 'account'

/** "Demais" in the lines: lighter than any group's gray. */
const REST_COLOR = 'color-mix(in oklab, var(--muted-foreground) 45%, var(--card))'

/** Names of what is open, carried in the navigation state so the
 *  breadcrumb can show them. A link opened cold has none: "…" stands in. */
interface Labels {
  group?: string
  category?: string
  merchant?: string
  account?: string
}

const VIEW_ICONS: Record<ExploreView, LucideIcon> = {
  group: Tags,
  merchant: Store,
  month: CalendarRange,
  account: CreditCard,
  compare: Scale,
}
const CHART_ICONS: Record<ExploreChart, LucideIcon> = {
  bars: BarChart3,
  treemap: LayoutGrid,
  trend: LineChart,
}

/** A row of choices in the period control's style (same height and frame). */
function Choice<T extends string>({
  label,
  value,
  options,
  onChange,
  testIdPrefix,
}: {
  label: string
  value: T
  options: { value: T; label: string; icon: LucideIcon }[]
  onChange: (value: T) => void
  testIdPrefix: string
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex w-full min-w-0 items-center gap-0.5 rounded-lg border border-border bg-muted p-0.5 text-xs font-medium sm:inline-flex sm:w-auto">
      {options.map((option) => {
        const active = option.value === value
        const Icon = option.icon
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            data-testid={`${testIdPrefix}-${option.value}`}
            onClick={() => onChange(option.value)}
            className={cn(
              'flex min-w-0 flex-auto items-center justify-center gap-1.5 rounded-md px-1.5 py-1.5 whitespace-nowrap transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:flex-none sm:px-2.5',
              active ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <Icon size={13} aria-hidden="true" className="hidden shrink-0 sm:block" />
            <span className="truncate">{option.label}</span>
          </button>
        )
      })}
    </div>
  )
}

export function ExploreTab() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const palette = useGroupPalette()
  const [searchParams, setSearchParams] = useSearchParams()
  const location = useLocation()
  const { period, from, to, query: periodQuery } = useReportPeriod()
  const carried: Labels = (location.state as { exploreLabels?: Labels } | null)?.exploreLabels ?? {}

  const view = parseView(searchParams.get('view'))
  const chart = parseChart(searchParams.get('chart'))
  const account = view === 'group' ? searchParams.get('account') : null
  const group = view === 'group' || view === 'compare' ? searchParams.get('group') : null
  const category = view === 'group' ? searchParams.get('category') : null
  const merchant = view === 'group' ? (category ? searchParams.get('merchant') : null) : view === 'merchant' ? searchParams.get('merchant') : null
  const monthMode = searchParams.get('mm') === 'categories' ? 'categories' : 'groups'
  const compareMode = parseCompare(searchParams.get('cmp'))
  const compareFrom = searchParams.get('cmp_from') ?? ''
  const compareTo = searchParams.get('cmp_to') ?? ''

  // A link opened cold carries no names: the group and category come from
  // the workspace's own list.
  const stateLabels: Labels = {
    ...carried,
    group: carried.group ?? (group && group !== 'none' ? palette.groupName(group) : undefined),
    category: carried.category ?? (category && category !== 'none' ? palette.categoryName(category) : undefined),
  }

  const level: Level =
    view === 'merchant' ? 'merchant' : view === 'account' ? 'account' : category ? 'merchant' : group ? 'category' : 'group'

  // Going down (or up) pushes a history entry, so Back climbs one level.
  const go = (patch: Record<string, string | null>, labels: Labels, replace = false) =>
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev)
        for (const [key, value] of Object.entries(patch)) {
          if (value === null) params.delete(key)
          else params.set(key, value)
        }
        return params
      },
      { state: { exploreLabels: labels }, replace },
    )
  const setView = (next: ExploreView) =>
    go({ view: next === 'group' ? null : next, group: null, category: null, merchant: null, account: null }, {})
  const setChart = (next: ExploreChart) => go({ chart: next === 'bars' ? null : next }, stateLabels, true)

  const scope = { period, ...(period === 'custom' ? { from, to } : {}) }
  const params: BreakdownParams | null =
    view === 'month' || view === 'compare'
      ? null
      : {
          level,
          ...(view === 'merchant' ? { category_id: 'all' } : {}),
          ...(view === 'group' && level === 'category' && group ? { group_id: group } : {}),
          ...(view === 'group' && level === 'merchant' && category ? { category_id: category } : {}),
          ...(account ? { account_id: account } : {}),
          ...scope,
        }
  const query = useQuery({
    queryKey: ['reports', 'breakdown', params],
    queryFn: () => reports.breakdown(params!),
    enabled: params !== null,
    // Only a period switch keeps the old list; another level is another list.
    placeholderData: (previous, previousQuery) => {
      const before = previousQuery?.queryKey[2] as BreakdownParams | undefined
      return before?.level === params?.level &&
        before?.group_id === params?.group_id &&
        before?.category_id === params?.category_id &&
        before?.account_id === params?.account_id
        ? previous
        : undefined
    },
  })
  const data = query.data
  // Month and Compare do not report: the period bar shows the dates the
  // period means.
  useReportedRange(params ? data?.period : null)
  const stale = query.isPlaceholderData

  const open = (row: BreakdownRow) => {
    if (view === 'account') {
      if (row.id) go({ view: null, account: row.id, group: null, category: null, merchant: null }, { account: row.label })
      return
    }
    if (view === 'merchant') {
      if (row.id) go({ merchant: row.id }, { merchant: row.label, category: row.category_label ?? undefined })
      return
    }
    const step = nextStep(level as 'group' | 'category' | 'merchant', row)
    if (!step) return
    const base = { account: stateLabels.account }
    if ('group' in step) go({ group: step.group, category: null, merchant: null }, { ...base, group: row.label })
    else if ('category' in step) {
      go({ category: step.category, merchant: null }, { ...base, group: stateLabels.group, category: row.label })
    } else go({ merchant: step.merchant }, { ...stateLabels, merchant: row.label })
  }

  // Colors: a group's own; below it, a shade of it; a store in the
  // all-stores view wears its category's group; accounts the fixed palette.
  const accountColor = useMemo(
    () => accountColors(level === 'account' ? (data?.rows ?? []).map((r) => r.id ?? '') : []),
    [level, data?.rows],
  )
  const groupColor = group && group !== 'none' ? palette.group(group) : NEUTRAL_COLOR
  const colorOf = (row: BreakdownRow) => {
    if (level === 'account') return accountColor.get(row.id ?? '') ?? NEUTRAL_COLOR
    if (view === 'merchant') return palette.group(row.category_id ? palette.groupOfCategory(row.category_id) : null)
    if (level === 'group') return palette.group(row.id, row.label)
    if (level === 'category') {
      const own = palette.category(row.id, row.label)
      return own === NEUTRAL_COLOR ? groupColor : own
    }
    const own = category && category !== 'none' ? palette.category(category) : NEUTRAL_COLOR
    return own === NEUTRAL_COLOR ? groupColor : own
  }
  const iconOf =
    level === 'group'
      ? (row: BreakdownRow) => (row.kind === 'category' ? palette.categoryIcon(row.id) : palette.groupIcon(row.id))
      : level === 'category'
        ? (row: BreakdownRow) => palette.categoryIcon(row.id)
        : undefined
  const sublabel = view === 'merchant' ? (row: BreakdownRow) => row.category_label : undefined

  // The selected store: only the store is shown, with a way back.
  const merchantRow = merchant ? data?.rows.find((r) => r.id === merchant) : undefined
  const merchantLabel = merchantRow?.label ?? stateLabels.merchant ?? '…'
  const storeCategoryLabel = view === 'merchant' ? (merchantRow?.category_label ?? stateLabels.category) : stateLabels.category

  const crumbs: { key: string; label: string; onClick?: () => void }[] = [
    { key: 'root', label: t('reports.explore.root'), onClick: () => go({ group: null, category: null, merchant: null }, { account: stateLabels.account }) },
  ]
  if (view === 'group' && group) {
    crumbs.push({
      key: 'group',
      label: stateLabels.group ?? '…',
      onClick: level === 'category' ? undefined : () => go({ category: null, merchant: null }, { account: stateLabels.account, group: stateLabels.group }),
    })
  }
  if (view === 'group' && category) {
    crumbs.push({
      key: 'category',
      label: stateLabels.category ?? '…',
      onClick: merchant ? () => go({ merchant: null }, { ...stateLabels, merchant: undefined }) : undefined,
    })
  }
  if (merchant && view === 'group') crumbs.push({ key: 'merchant', label: merchantLabel })

  const ranking = RANKING_VIEWS.includes(view) && !merchant
  const months = data ? sparklineMonths(data.period.to) : []

  return (
    <div className="space-y-5" data-testid="explore-tab">
      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
        <Choice
          label={t('reports.explore.viewBy')}
          value={view}
          onChange={setView}
          testIdPrefix="explore-view"
          options={(['group', 'merchant', 'month', 'account', 'compare'] as ExploreView[]).map((v) => ({
            value: v,
            label: t(`reports.explore.view.${v}`),
            icon: VIEW_ICONS[v],
          }))}
        />
        {ranking && (
          <Choice
            label={t('reports.explore.showAs')}
            value={chart}
            onChange={setChart}
            testIdPrefix="explore-chart"
            options={(['bars', 'treemap', 'trend'] as ExploreChart[]).map((c) => ({
              value: c,
              label: t(`reports.explore.chart.${c}`),
              icon: CHART_ICONS[c],
            }))}
          />
        )}
      </div>

      {account && (
        <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="explore-account-filter">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card py-1 pl-3 pr-1 text-xs font-medium text-foreground">
            <CreditCard size={12} aria-hidden="true" className="text-muted-foreground" />
            {t('reports.explore.accountFilter', { name: stateLabels.account ?? '…' })}
            <button
              type="button"
              onClick={() => go({ account: null }, { ...stateLabels, account: undefined })}
              aria-label={t('reports.explore.clearAccount')}
              className="rounded-full p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <X size={12} aria-hidden="true" />
            </button>
          </span>
        </div>
      )}

      {view === 'group' && level !== 'group' && (
        <nav aria-label={t('reports.explore.breadcrumb')}>
          <ol className="flex flex-wrap items-center gap-1 text-sm">
            {crumbs.map((crumb, i) => (
              <li key={crumb.key} className="flex items-center gap-1">
                {i > 0 && <ChevronRight size={14} className="text-muted-foreground" aria-hidden="true" />}
                {crumb.onClick ? (
                  <button type="button" onClick={crumb.onClick} className="rounded text-muted-foreground hover:text-foreground hover:underline">
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

      {view === 'month' ? (
        <MonthView
          scope={scope}
          mode={monthMode}
          onMode={(mode) => go({ mm: mode === 'groups' ? null : mode }, {}, true)}
          onOpen={(row, month, isCategory) => {
            const patch: Record<string, string | null> = { view: null, merchant: null, account: null }
            const labels: Labels = {}
            if (isCategory) {
              const g = row.id ? palette.groupOfCategory(row.id) : null
              patch.group = g ?? 'none'
              patch.category = row.id ?? 'none'
              labels.group = g ? palette.groupName(g) : (row.group_label ?? undefined)
              labels.category = row.label
            } else {
              const step = nextStep('group', row)
              if (step && 'group' in step) {
                patch.group = step.group
                patch.category = null
                labels.group = row.label
              } else if (step && 'category' in step) {
                patch.group = null
                patch.category = step.category
                labels.category = row.label
              }
            }
            if (month) {
              const range = monthRange(month)
              Object.assign(patch, { period: 'custom', from: range.from, to: range.to })
            }
            go(patch, labels)
          }}
          locale={locale}
        />
      ) : view === 'compare' ? (
        <CompareView
          query={periodQuery}
          mode={compareMode}
          from={compareFrom}
          to={compareTo}
          group={group}
          groupLabel={stateLabels.group}
          palette={palette}
          locale={locale}
          onMode={(mode: BreakdownCompare, range) =>
            go(
              {
                cmp: mode === 'previous' ? null : mode,
                cmp_from: mode === 'custom' && range ? range.from : mode === 'custom' ? compareFrom || null : null,
                cmp_to: mode === 'custom' && range ? range.to : mode === 'custom' ? compareTo || null : null,
              },
              stateLabels,
              true,
            )
          }
          onOpenGroup={(row) => row.id && go({ group: row.id }, { group: row.label })}
          onBack={() => go({ group: null }, {})}
        />
      ) : query.isError && !data ? (
        <BlockError onRetry={() => query.refetch()} message={t('reports.explore.error')} />
      ) : !data ? (
        <div className="space-y-3" aria-busy="true" data-testid="explore-skeleton">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-56 w-full" />
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
      ) : merchant ? (
        <StorePanel
          key={`${view}:${category}:${merchant}`}
          data={data}
          row={merchantRow}
          merchant={merchant}
          merchantLabel={merchantLabel}
          categoryLabel={storeCategoryLabel}
          categoryId={view === 'merchant' ? 'all' : category!}
          accountId={account}
          backLabel={
            view === 'merchant'
              ? t('reports.explore.backToStores')
              : t('reports.explore.backToCategory', { category: stateLabels.category ?? '…' })
          }
          onBack={() => go({ merchant: null }, { ...stateLabels, merchant: undefined })}
          locale={locale}
        />
      ) : (
        <FadePanel panelKey={`${view}:${level}:${group ?? ''}:${category ?? ''}:${account ?? ''}`} className="space-y-4">
          <div
            aria-busy={stale}
            data-testid="explore-content"
            className={cn('space-y-4 transition-opacity duration-200', stale && 'opacity-60')}
          >
            <TotalCard data={data} locale={locale} level={level} rows={data.rows} />

            {data.unconverted_count > 0 && (
              <p className="text-xs text-amber-700 dark:text-amber-400">
                {t('reports.explore.unconverted', { n: data.unconverted_count })}
              </p>
            )}

            {data.rows.length === 0 ? (
              <p className="rounded-xl border border-dashed border-border py-10 text-center text-sm text-muted-foreground">
                {t('reports.explore.empty', {
                  from: monthLabel(data.period.from, locale),
                  to: monthLabel(data.period.to, locale),
                })}
              </p>
            ) : (
              <>
                {data.rows.some((r) => Number(r.total) > 0) && (
                  <div className="rounded-xl border border-border bg-card p-3 shadow-sm sm:p-4" data-testid="explore-chart">
                    {chart === 'treemap' ? (
                      <ExploreTreemap rows={data.rows} level={level} currency={data.currency} locale={locale} colorOf={colorOf} onOpen={open} selected={merchant} />
                    ) : chart === 'trend' ? (
                      <TrendChart rows={data.rows} months={months} colorOf={colorOf} level={level} view={view} onOpen={open} currency={data.currency} locale={locale} />
                    ) : (
                      <ExploreBars
                        rows={data.rows}
                        currency={data.currency}
                        locale={locale}
                        colorOf={colorOf}
                        iconOf={iconOf}
                        sublabel={sublabel}
                        onOpen={open}
                        ariaLabel={t('reports.explore.bars.aria', { name: data.rows[0]?.label ?? '' })}
                      />
                    )}
                  </div>
                )}
                <ExploreTable
                  data={data}
                  level={level}
                  locale={locale}
                  colorOf={colorOf}
                  iconOf={iconOf}
                  sublabel={sublabel}
                  onOpen={open}
                  mask={mask}
                />
              </>
            )}
          </div>
        </FadePanel>
      )}
    </div>
  )
}

/** The total of the list, big, with its change against the period before
 *  and, when a few rows make most of it, that fact in one line. */
function TotalCard({ data, locale, level, rows }: { data: BreakdownResponse; locale: string; level: Level; rows: BreakdownRow[] }) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const total = Number(data.total)
  const previous = data.previous_total != null ? Number(data.previous_total) : 0
  const change = previous > 0 ? (total - previous) / previous : null
  const head = concentration(rows)
  return (
    <div className="rounded-xl border border-border bg-card px-4 py-4 shadow-sm sm:px-5">
      <p className="text-xs font-medium text-muted-foreground">{t('reports.explore.spending')}</p>
      <div className="mt-0.5 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <p className="text-3xl font-semibold text-foreground" data-testid="explore-total">
          {mask(formatCurrency(total, data.currency, locale))}
        </p>
        {change !== null && Math.abs(change) >= 0.005 && (
          <span className="text-sm text-muted-foreground">
            <DeltaPill change={change} good={change < 0} locale={locale} /> {t('reports.explore.vsBefore')}
          </span>
        )}
      </div>
      {head && (
        <p className="mt-2 text-sm text-muted-foreground" data-testid="explore-concentration">
          {t(`reports.explore.concentration.${level}`, { n: head.n, m: head.m })}
        </p>
      )}
    </div>
  )
}

function TrendChart({
  rows,
  months,
  colorOf,
  level,
  view,
  onOpen,
  currency,
  locale,
}: {
  rows: BreakdownRow[]
  months: string[]
  colorOf: (row: BreakdownRow) => string
  level: Level
  view: ExploreView
  onOpen: (row: BreakdownRow) => void
  currency: string
  locale: string
}) {
  const { t } = useTranslation()
  const series = trendSeries(rows, t('reports.explore.trend.rest'))
  // Groups and accounts have colors of their own; categories of one group
  // (and stores) are told apart by the fixed palette, in order.
  const own = (level === 'group' || level === 'account') && view !== 'merchant'
  const color = (s: TrendSeries, i: number) =>
    s.rest || !s.row ? REST_COLOR : own ? colorOf(s.row) : `var(--cat-${i + 1})`
  const indexOf = new Map(series.map((s, i) => [s.key, i]))
  const top = series[0]
  return (
    <ExploreTrend
      series={series}
      months={months}
      colorOf={(s) => color(s, indexOf.get(s.key) ?? 0)}
      onOpen={onOpen}
      currency={currency}
      locale={locale}
      ariaLabel={top ? t('reports.explore.trend.aria', { name: top.label }) : ''}
    />
  )
}

function ExploreTable({
  data,
  level,
  locale,
  colorOf,
  iconOf,
  sublabel,
  onOpen,
  mask,
}: {
  data: BreakdownResponse
  level: Level
  locale: string
  colorOf: (row: BreakdownRow) => string
  iconOf?: (row: BreakdownRow) => string | null
  sublabel?: (row: BreakdownRow) => string | null | undefined
  onOpen: (row: BreakdownRow) => void
  mask: (value: string) => string
}) {
  const { t } = useTranslation()
  return (
    <div className="rounded-xl border border-border bg-card shadow-sm">
      <table className="w-full table-fixed text-sm" data-testid="explore-table">
        <thead>
          <tr className="border-b border-border text-left text-xs font-medium text-muted-foreground">
            <th className="px-3 py-2.5 sm:px-4">{t(`reports.explore.col.${level}`)}</th>
            <th className="w-24 px-2 py-2.5 text-right sm:w-32">{t('reports.explore.col.total')}</th>
            <th className="w-12 px-2 py-2.5 text-right sm:w-16">{t('reports.explore.col.share')}</th>
            <th className="w-16 px-2 py-2.5 text-right sm:w-20">{t('reports.explore.col.change')}</th>
            <th className="hidden w-24 px-2 py-2.5 text-right sm:table-cell">{t('reports.explore.col.count')}</th>
            <th className="hidden w-24 px-3 py-2.5 sm:table-cell sm:px-4">{t('reports.explore.col.trend')}</th>
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row) => {
            const value = Number(row.total)
            const offsets = value < 0
            const sub = sublabel?.(row)
            return (
              <tr
                key={row.id ?? row.label}
                tabIndex={0}
                data-testid="explore-row"
                onClick={() => onOpen(row)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') onOpen(row)
                }}
                className="cursor-pointer border-b border-border last:border-b-0 hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
              >
                <td className="px-3 py-2.5 sm:px-4">
                  <span className="flex min-w-0 items-center gap-2">
                    {iconOf ? (
                      <CategoryIcon icon={iconOf(row)} color={colorOf(row)} size="xs" />
                    ) : (
                      <span aria-hidden="true" className="size-2.5 shrink-0 rounded-sm" style={{ background: colorOf(row) }} />
                    )}
                    <span className="min-w-0">
                      <span className="block truncate font-medium text-foreground">{row.label}</span>
                      {sub && <span className="block truncate text-[11px] text-muted-foreground">{sub}</span>}
                    </span>
                  </span>
                </td>
                <td className="px-2 py-2.5 text-right tabular-nums">{mask(formatCurrency(value, data.currency, locale))}</td>
                <td className="px-2 py-2.5 text-right tabular-nums text-muted-foreground">
                  {offsets ? t('reports.explore.offsets') : row.share != null ? formatPercent(row.share, locale) : '—'}
                </td>
                <td className={cn('px-2 py-2.5 text-right tabular-nums', row.change === null ? 'text-muted-foreground' : changeTone(row.change))}>
                  {row.change === null ? '—' : formatChange(row.change, locale)}
                </td>
                <td className="hidden px-2 py-2.5 text-right tabular-nums text-muted-foreground sm:table-cell">{row.count}</td>
                <td className="hidden px-3 py-2.5 text-primary sm:table-cell sm:px-4">
                  <Sparkline values={row.sparkline} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** One store, alone on the screen: its name and category, what it cost and
 *  how many times, a way back to the list, and its transactions. */
function StorePanel({
  data,
  row,
  merchant,
  merchantLabel,
  categoryLabel,
  categoryId,
  accountId,
  backLabel,
  onBack,
  locale,
}: {
  data: BreakdownResponse
  row?: BreakdownRow
  merchant: string
  merchantLabel: string
  categoryLabel?: string | null
  categoryId: string
  accountId: string | null
  backLabel: string
  onBack: () => void
  locale: string
}) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  return (
    <FadePanel panelKey={`store:${merchant}`} className="space-y-4" data-testid="explore-store">
      <Button type="button" variant="ghost" size="sm" onClick={onBack} className="-ml-3 h-8 text-muted-foreground" data-testid="explore-store-back">
        <ArrowLeft aria-hidden="true" />
        {backLabel}
      </Button>
      <div className="rounded-xl border border-border bg-card px-4 py-4 shadow-sm sm:px-5">
        <h3 className="text-base font-semibold text-foreground" data-testid="explore-store-title">
          {merchantLabel}
          {categoryLabel && <span className="font-normal text-muted-foreground"> · {categoryLabel}</span>}
        </h3>
        {row && (
          <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <p className="text-3xl font-semibold text-foreground">{mask(formatCurrency(Number(row.total), data.currency, locale))}</p>
            <p className="text-sm text-muted-foreground">{t('reports.explore.storeCount', { count: row.count })}</p>
          </div>
        )}
        <p className="mt-1 text-xs text-muted-foreground">
          {t('reports.explore.rangeClosed', { from: monthLabel(data.period.from, locale), to: monthLabel(data.period.to, locale) })}
        </p>
      </div>
      <MerchantTransactions
        merchantLabel={merchantLabel}
        merchantKey={merchant}
        categoryId={categoryId}
        accountId={accountId}
        range={{ from: data.period.from, to: data.period.to }}
      />
    </FadePanel>
  )
}

/** "Mês": groups (or every category) by month, as a table of colors. */
function MonthView({
  scope,
  mode,
  onMode,
  onOpen,
  locale,
}: {
  scope: { period: BreakdownParams['period']; from?: string; to?: string }
  mode: 'groups' | 'categories'
  onMode: (mode: 'groups' | 'categories') => void
  onOpen: (row: BreakdownRow, month: string | null, isCategory: boolean) => void
  locale: string
}) {
  const { t } = useTranslation()
  const params: BreakdownParams =
    mode === 'groups' ? { level: 'group', ...scope } : { level: 'category', group_id: 'all', ...scope }
  const query = useQuery({
    queryKey: ['reports', 'breakdown', params],
    queryFn: () => reports.breakdown(params),
    placeholderData: (previous, previousQuery) =>
      (previousQuery?.queryKey[2] as BreakdownParams | undefined)?.level === params.level ? previous : undefined,
  })
  const data = query.data
  const months = data ? sparklineMonths(data.period.to) : []
  const isCategory = mode === 'categories'
  const top = data?.rows[0]

  return (
    <div className="space-y-4" data-testid="explore-month">
      <Choice
        label={t('reports.explore.month.mode')}
        value={mode}
        onChange={onMode}
        testIdPrefix="month-mode"
        options={[
          { value: 'groups', label: t('reports.explore.month.groups'), icon: Tags },
          { value: 'categories', label: t('reports.explore.month.categories'), icon: LayoutGrid },
        ]}
      />
      {query.isError && !data ? (
        <BlockError onRetry={() => query.refetch()} message={t('reports.explore.error')} />
      ) : !data ? (
        <Skeleton className="h-72 w-full" />
      ) : data.rows.length === 0 ? (
        <p className="rounded-xl border border-dashed border-border py-10 text-center text-sm text-muted-foreground">
          {t('reports.explore.empty', { from: monthLabel(data.period.from, locale), to: monthLabel(data.period.to, locale) })}
        </p>
      ) : (
        <div
          className={cn('rounded-xl border border-border bg-card p-3 shadow-sm transition-opacity sm:p-4', query.isPlaceholderData && 'opacity-60')}
          aria-busy={query.isPlaceholderData || undefined}
        >
          <MonthMatrix
            rows={data.rows}
            months={months}
            currency={data.currency}
            locale={locale}
            sublabel={isCategory ? (row) => row.group_label : undefined}
            onOpenCell={(row, month) => onOpen(row, month, isCategory)}
            onOpenRow={(row) => onOpen(row, null, isCategory)}
            ariaLabel={top ? t('reports.explore.month.aria', { name: top.label }) : t('reports.explore.view.month')}
          />
        </div>
      )}
    </div>
  )
}
