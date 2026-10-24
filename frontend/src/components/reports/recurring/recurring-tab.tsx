/** Reports > Fixos: the charges that repeat, found from the history (no
 *  sign-up needed). The answer first: how much the fixed costs take a month
 *  and a year, and what share of the spending that is; then each active
 *  charge with its status (new, up, steady), most expensive per year first,
 *  and at the end the ones that ended, with their last charge. A click opens
 *  the store in Explore; "Já acabou" (people who can edit) ends one now. */
import { useContext, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { ArrowUpRight, CircleCheck, CirclePause, MoreHorizontal, Repeat, Sparkles, type LucideIcon } from 'lucide-react'
import { reports, type RecurringRow, type RecurringStatus } from '@/lib/api'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { Segmented } from '@/components/invoice-ui'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ReportBlock } from '@/components/reports/common/report-block'
import { AnswerLine } from '@/components/reports/common/answer-line'
import { answerParts } from '@/components/reports/common/answer-parts'
import { blockState } from '@/components/reports/common/block-state'
import { formatMoneyShort, formatPercent } from '@/components/reports/common/chart-utils'
import { useGroupPalette } from '@/components/reports/common/group-colors'
import { exploreLink } from '@/components/reports/common/report-url'
import { fixedSummary, sortRecurring, type RecurringSort } from './recurring-model'

const STATUS: Record<RecurringStatus, { icon: LucideIcon; tone: string }> = {
  new: { icon: Sparkles, tone: 'bg-sky-50 text-sky-700 border-sky-100 dark:bg-sky-500/10 dark:text-sky-300 dark:border-sky-500/20' },
  up: { icon: ArrowUpRight, tone: 'bg-amber-50 text-amber-700 border-amber-100 dark:bg-amber-500/10 dark:text-amber-300 dark:border-amber-500/20' },
  stopped: { icon: CirclePause, tone: 'bg-muted text-muted-foreground border-border' },
  steady: { icon: CircleCheck, tone: 'bg-emerald-50 text-emerald-700 border-emerald-100 dark:bg-emerald-500/10 dark:text-emerald-300 dark:border-emerald-500/20' },
}

function StatusBadge({ status }: { status: RecurringStatus }) {
  const { t } = useTranslation()
  const { icon: Icon, tone } = STATUS[status]
  return (
    <span
      data-testid={`status-${status}`}
      className={cn('inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-semibold', tone)}
    >
      <Icon size={11} aria-hidden="true" />
      {t(`reports.recurring.status.${status}`)}
    </span>
  )
}

type Filter = 'all' | Exclude<RecurringStatus, 'stopped'>

const RECURRING_KEY = ['reports', 'recurring'] as const

export function RecurringTab() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  // Outside a workspace (a bare render) nothing can be changed.
  const canWrite = useContext(WorkspaceContext)?.canWrite ?? false
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const palette = useGroupPalette()
  const query = { period: 'last_12m' as const }
  const [sort, setSort] = useState<RecurringSort>('year')
  const [filter, setFilter] = useState<Filter>('all')

  // The detection always reads the last 12 closed months and the current one,
  // whatever the period: one query for every period.
  const recurring = useQuery({ queryKey: RECURRING_KEY, queryFn: () => reports.recurring({ period: 'last_12m' }) })
  // The period's spending, to say what share of it is fixed.
  const overview = useQuery({
    queryKey: ['reports', 'overview', query],
    queryFn: () => reports.overview(query),
    placeholderData: keepPreviousData,
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: RECURRING_KEY })
    void queryClient.invalidateQueries({ queryKey: ['reports', 'overview'] })
  }
  const undoEnd = useMutation({
    mutationFn: (key: string) => reports.undoEndRecurring(key),
    onSuccess: refresh,
    onError: () => toast.error(t('reports.recurring.endError')),
  })
  const end = useMutation({
    mutationFn: (key: string) => reports.endRecurring(key),
    onSuccess: (_, key) => {
      refresh()
      toast.success(t('reports.recurring.endedToast'), {
        action: { label: t('reports.recurring.undo'), onClick: () => undoEnd.mutate(key) },
        duration: 8000,
      })
    },
    onError: () => toast.error(t('reports.recurring.endError')),
  })

  const currency = overview.data?.currency ?? user?.preferences?.currency_display ?? 'BRL'
  const money = (v: number) => mask(formatMoneyShort(v, currency, locale))
  const rows = useMemo(() => recurring.data ?? [], [recurring.data])
  const active = useMemo(() => rows.filter((r) => r.status !== 'stopped'), [rows])
  const ended = useMemo(
    () => rows.filter((r) => r.status === 'stopped').sort((a, b) => b.last_date.localeCompare(a.last_date)),
    [rows],
  )
  const summary = fixedSummary(rows, overview.data?.kpis.spending)
  const visible = useMemo(
    () => sortRecurring(filter === 'all' ? active : active.filter((r) => r.status === filter), sort),
    [active, filter, sort],
  )
  const maxYear = Math.max(...active.map((r) => Number(r.amount_year)), 1)

  const headline = recurring.data
    ? active.length === 0
      ? null
      : summary.share !== null
        ? answerParts(t, 'reports.recurring.headlineShare', {
            month: { kind: 'value', text: money(summary.month) },
            year: { kind: 'value', text: money(summary.year) },
            share: { kind: 'value', text: formatPercent(summary.share, locale) },
          })
        : answerParts(t, 'reports.recurring.headline', {
            month: { kind: 'value', text: money(summary.month) },
            year: { kind: 'value', text: money(summary.year) },
          })
    : null

  const open = (row: RecurringRow) => {
    const category = row.category_id ?? 'none'
    const group = row.category_id ? palette.groupOfCategory(row.category_id) : null
    const link = exploreLink(
      { ...query, group, category, merchant: row.merchant_key },
      { group: group ? palette.groupName(group) : undefined, category: row.category, merchant: row.label },
      overview.data?.period,
    )
    navigate(link.to, { state: link.state })
  }

  const counts = (s: RecurringStatus) => rows.filter((r) => r.status === s).length
  const dateFmt = (iso: string) =>
    new Date(`${iso}T00:00:00`).toLocaleDateString(locale, { day: '2-digit', month: 'short', year: '2-digit' })

  return (
    <div className="space-y-5" data-testid="recurring-tab">
      <AnswerLine
        parts={headline}
        icon={Repeat}
        loading={!recurring.data && !recurring.isError}
        note={
          recurring.data && rows.length > 0
            ? t('reports.recurring.counts', {
                active: active.length,
                new: counts('new'),
                up: counts('up'),
                stopped: ended.length,
              })
            : undefined
        }
      />

      <ReportBlock
        question={t('reports.recurring.question')}
        title={t('reports.recurring.title', { count: active.length })}
        description={t('reports.recurring.description')}
        state={blockState(recurring, rows.length === 0)}
        onRetry={() => recurring.refetch()}
        emptyText={t('reports.recurring.empty')}
        skeletonHeight={320}
        testId="block-recurring"
        action={
          <Segmented
            value={sort}
            onChange={setSort}
            testIdPrefix="recurring-sort"
            options={[
              { value: 'year', label: t('reports.recurring.sort.year') },
              { value: 'recent', label: t('reports.recurring.sort.recent') },
              { value: 'name', label: t('reports.recurring.sort.name') },
            ]}
          />
        }
      >
        <div className="mb-3 overflow-x-auto">
          <Segmented
            value={filter}
            onChange={setFilter}
            testIdPrefix="recurring-filter"
            options={[
              { value: 'all', label: t('reports.recurring.filter.all'), count: active.length },
              { value: 'new', label: t('reports.recurring.status.new'), count: counts('new') },
              { value: 'up', label: t('reports.recurring.status.up'), count: counts('up') },
              { value: 'steady', label: t('reports.recurring.status.steady'), count: counts('steady') },
            ]}
          />
        </div>
        <div className="hidden grid-cols-[minmax(0,1fr)_6rem_6.5rem_7rem_8rem_2rem] gap-3 border-b border-border px-2 pb-2 text-xs font-medium text-muted-foreground sm:grid">
          <span>{t('reports.recurring.col.store')}</span>
          <span>{t('reports.recurring.col.status')}</span>
          <span>{t('reports.recurring.col.last')}</span>
          <span className="text-right">{t('reports.recurring.col.month')}</span>
          <span className="text-right">{t('reports.recurring.col.year')}</span>
          <span />
        </div>
        <ul className="divide-y divide-border" data-testid="recurring-list">
          {visible.map((row) => {
            const year = Number(row.amount_year)
            return (
              <li key={row.merchant_key} className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => open(row)}
                  data-testid="recurring-row"
                  aria-label={t('reports.recurring.rowAria', {
                    name: row.label,
                    status: t(`reports.recurring.status.${row.status}`),
                    month: mask(formatCurrency(Number(row.amount_month), currency, locale)),
                    year: mask(formatCurrency(year, currency, locale)),
                  })}
                  className="grid min-w-0 flex-1 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 rounded-md px-2 py-2.5 text-left text-sm transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:grid-cols-[minmax(0,1fr)_6rem_6.5rem_7rem_8rem]"
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium text-foreground">{row.label}</span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {row.category}
                      <span className="sm:hidden"> · {dateFmt(row.last_date)}</span>
                    </span>
                  </span>
                  <span className="justify-self-end sm:justify-self-start">
                    <StatusBadge status={row.status} />
                  </span>
                  <span className="hidden text-xs tabular-nums text-muted-foreground sm:block">{dateFmt(row.last_date)}</span>
                  <span className="col-start-1 text-xs tabular-nums text-muted-foreground sm:col-start-auto sm:text-right sm:text-sm sm:text-foreground">
                    {t('reports.recurring.perMonth', { value: mask(formatCurrency(Number(row.amount_month), currency, locale)) })}
                  </span>
                  <span className="col-start-2 row-start-2 text-right sm:col-start-auto sm:row-start-auto">
                    <span className="block font-semibold tabular-nums text-foreground">
                      {t('reports.recurring.perYear', { value: mask(formatMoneyShort(year, currency, locale)) })}
                    </span>
                    {/* How heavy this charge is next to the biggest one. */}
                    <span className="ml-auto mt-1 block h-1 w-20 rounded-full bg-muted" aria-hidden="true">
                      <span className="block h-1 rounded-full bg-primary/70" style={{ width: `${Math.max(4, (year / maxYear) * 100)}%` }} />
                    </span>
                  </span>
                </button>
                {canWrite ? (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        className="size-8 shrink-0 text-muted-foreground"
                        aria-label={t('reports.recurring.menu', { name: row.label })}
                        data-testid="recurring-menu"
                      >
                        <MoreHorizontal size={16} aria-hidden="true" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem
                        data-testid="recurring-end"
                        disabled={end.isPending}
                        onSelect={() => end.mutate(row.merchant_key)}
                      >
                        <CirclePause size={14} aria-hidden="true" />
                        {t('reports.recurring.end')}
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                ) : (
                  <span className="hidden w-8 shrink-0 sm:block" />
                )}
              </li>
            )
          })}
        </ul>
        {visible.length === 0 && <p className="py-6 text-center text-sm text-muted-foreground">{t('reports.recurring.noneInFilter')}</p>}

        {ended.length > 0 && (
          <section className="mt-6" aria-labelledby="recurring-ended-title" data-testid="recurring-ended">
            <h3 id="recurring-ended-title" className="flex items-center gap-2 border-b border-border px-2 pb-2 text-xs font-medium text-muted-foreground">
              <CirclePause size={13} aria-hidden="true" />
              {t('reports.recurring.endedTitle', { count: ended.length })}
            </h3>
            <ul className="divide-y divide-border">
              {ended.map((row) => (
                <li key={row.merchant_key} className="flex items-center gap-2" data-testid="recurring-ended-row">
                  <button
                    type="button"
                    onClick={() => open(row)}
                    className="flex min-w-0 flex-1 items-center justify-between gap-3 rounded-md px-2 py-2 text-left text-sm text-muted-foreground transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <span className="min-w-0">
                      <span className="block truncate font-medium">{row.label}</span>
                      <span className="block truncate text-xs">
                        {t('reports.recurring.lastCharge', { date: dateFmt(row.last_date) })}
                      </span>
                    </span>
                    <span className="shrink-0 text-xs tabular-nums">
                      {t('reports.recurring.perMonth', { value: mask(formatCurrency(Number(row.amount_month), currency, locale)) })}
                    </span>
                  </button>
                  {canWrite && row.ended_on && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      className="shrink-0"
                      data-testid="recurring-undo"
                      disabled={undoEnd.isPending}
                      onClick={() => undoEnd.mutate(row.merchant_key)}
                    >
                      {t('reports.recurring.undo')}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}
        <p className="mt-3 text-[11px] text-muted-foreground">{t('reports.recurring.window')}</p>
      </ReportBlock>
    </div>
  )
}
