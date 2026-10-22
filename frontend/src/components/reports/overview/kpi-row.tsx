/** The four numbers of the Overview: spending, income, what was left (or
 *  missing) and how much of the spending is fixed. Monthly averages, each with
 *  its change against the previous period of the same length. */
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { ArrowDownRight, ArrowUpRight, ChevronRight } from 'lucide-react'
import type { OverviewResponse } from '@/lib/api'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { Skeleton } from '@/components/ui/skeleton'
import { formatMoneyShort, formatPercent } from '@/components/reports/common/chart-utils'

interface KpiRowProps {
  data: OverviewResponse | undefined
  previous: OverviewResponse | null | undefined
  currency: string
  locale: string
  stale?: boolean
  fixedHref: string
}

/** Whether a rise is good news for this number. */
type Polarity = 'up-good' | 'up-bad'

function Delta({ change, polarity, locale }: { change: number | null; polarity: Polarity; locale: string }) {
  const { t } = useTranslation()
  if (change === null || !Number.isFinite(change)) return null
  const flat = Math.abs(change) < 0.005
  const good = flat ? null : (change > 0) === (polarity === 'up-good')
  const Icon = change > 0 ? ArrowUpRight : ArrowDownRight
  return (
    <span
      className={cn(
        'inline-flex items-center gap-0.5 text-xs font-semibold',
        good === null
          ? 'text-muted-foreground'
          : good
            ? 'text-emerald-600 dark:text-emerald-400'
            : 'text-rose-600 dark:text-rose-400',
      )}
    >
      {!flat && <Icon size={13} aria-hidden="true" />}
      {t('reports.overview.kpi.vsPrevious', { change: formatPercent(change, locale, true) })}
    </span>
  )
}

function Tile({ label, children, testId }: { label: string; children: React.ReactNode; testId: string }) {
  return (
    <div data-testid={testId} className="min-w-0 rounded-xl border border-border bg-card px-4 py-3.5 shadow-sm">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      {children}
    </div>
  )
}

const ratio = (now: number, before: number | null) =>
  before === null || !(Math.abs(before) > 0) ? null : (now - before) / Math.abs(before)

export function KpiRow({ data, previous, currency, locale, stale, fixedHref }: KpiRowProps) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  if (!data) {
    return (
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-busy="true">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-[92px] w-full rounded-xl" />
        ))}
      </div>
    )
  }
  const k = data.kpis
  const spending = Number(k.spending)
  const income = Number(k.income)
  const left = Number(k.left)
  const comparable = previous && !data.period.partial ? previous.kpis : null
  const prev = (v: string | undefined) => (comparable && v !== undefined ? Number(v) : null)
  const short = left < 0
  const money = (v: number) => mask(formatCurrency(v, currency, locale))
  const fixed = k.fixed_share
  const leftDiff = comparable ? left - Number(comparable.left) : null

  return (
    <div
      className={cn('grid grid-cols-2 gap-3 transition-opacity duration-200 lg:grid-cols-4', stale && 'opacity-60')}
      aria-busy={stale || undefined}
    >
      <Tile label={t('reports.overview.kpi.spending')} testId="kpi-spending">
        <p className="mt-1 truncate text-xl font-semibold text-foreground sm:text-2xl">{money(spending)}</p>
        <Delta change={ratio(spending, prev(comparable?.spending))} polarity="up-bad" locale={locale} />
      </Tile>
      <Tile label={t('reports.overview.kpi.income')} testId="kpi-income">
        <p className="mt-1 truncate text-xl font-semibold text-foreground sm:text-2xl">{money(income)}</p>
        <Delta change={ratio(income, prev(comparable?.income))} polarity="up-good" locale={locale} />
      </Tile>
      <Tile label={t(short ? 'reports.overview.kpi.short' : 'reports.overview.kpi.left')} testId="kpi-left">
        <p
          className={cn(
            'mt-1 truncate text-xl font-semibold sm:text-2xl',
            short ? 'text-rose-600 dark:text-rose-400' : 'text-foreground',
          )}
        >
          {money(Math.abs(left))}
        </p>
        <p className="text-xs text-muted-foreground">
          {k.left_share !== null
            ? t(short ? 'reports.overview.kpi.shortShare' : 'reports.overview.kpi.leftShare', {
                share: formatPercent(Math.abs(k.left_share), locale),
              })
            : t('reports.overview.kpi.noIncome')}
          {leftDiff !== null && Math.abs(leftDiff) >= 1 && (
            <span
              className={cn(
                'ml-1 font-semibold',
                leftDiff > 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400',
              )}
            >
              {t(leftDiff > 0 ? 'reports.overview.kpi.leftMore' : 'reports.overview.kpi.leftLess', {
                value: mask(formatMoneyShort(Math.abs(leftDiff), currency, locale)),
              })}
            </span>
          )}
        </p>
      </Tile>
      <Link
        to={fixedHref}
        data-testid="kpi-fixed"
        className="group min-w-0 rounded-xl border border-border bg-card px-4 py-3.5 shadow-sm transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <p className="flex items-center justify-between text-xs font-medium text-muted-foreground">
          {t('reports.overview.kpi.fixed')}
          <ChevronRight size={14} className="opacity-0 transition-opacity group-hover:opacity-100" aria-hidden="true" />
        </p>
        {fixed === null ? (
          <p className="mt-1 text-xl font-semibold text-muted-foreground sm:text-2xl">—</p>
        ) : (
          <>
            <p className="mt-1 text-xl font-semibold text-foreground sm:text-2xl">
              {t('reports.overview.kpi.fixedValue', { share: formatPercent(fixed, locale) })}
            </p>
            {/* Fixed against variable as one meter: same hue, lighter track. */}
            <div
              className="mt-1.5 flex h-1.5 overflow-hidden rounded-full bg-primary/20"
              role="img"
              aria-label={t('reports.overview.kpi.fixedAria', {
                fixed: formatPercent(fixed, locale),
                variable: formatPercent(Math.max(0, 1 - fixed), locale),
              })}
            >
              <div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, fixed * 100)}%` }} />
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {t('reports.overview.kpi.fixedSub', {
                variable: formatPercent(Math.max(0, 1 - fixed), locale),
                value: mask(formatMoneyShort(fixed * spending, currency, locale)),
              })}
            </p>
          </>
        )}
      </Link>
    </div>
  )
}
