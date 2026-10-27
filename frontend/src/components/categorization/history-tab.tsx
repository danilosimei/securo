/** Histórico: every category change, one line per action (a save, "apply to
 *  the similar ones", a Revisar answer, a run), newest day first. A line
 *  says who did it, in which stores, from which category to which, and
 *  undoes whatever of it nobody changed since; opening it lists its rows.
 *  A toast's "See in Histórico" lands here with that line open
 *  (`?tab=history&action=<id>`). */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { keepPreviousData, useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  ArrowRight,
  ChevronRight,
  CircleDot,
  Combine,
  Loader2,
  Undo2,
  UserRound,
  type LucideIcon,
} from 'lucide-react'
import { categorization as categorizationApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateCategorizationQueries } from '@/lib/invalidate-queries'
import { isToday, isYesterday } from '@/lib/categorization-applied'
import { ORIGIN_STYLES } from '@/lib/categorization-origins'
import { formatCurrency } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useWorkspace } from '@/contexts/workspace-context'
import { useAuth } from '@/contexts/auth-context'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { Segmented } from '@/components/invoice-ui'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { HistoryAction, HistoryActor, HistoryWho } from '@/types'

const PAGE = 30
const ROWS_PAGE = 50

type Filter = 'all' | HistoryWho
const FILTERS: Filter[] = ['all', 'you', 'ai', 'auto']

const WHO_ICON: Record<HistoryActor, { icon: LucideIcon; className: string }> = {
  you: { icon: UserRound, className: 'text-primary' },
  undo: { icon: Undo2, className: 'text-primary' },
  ai: { icon: ORIGIN_STYLES.ai.Icon, className: ORIGIN_STYLES.ai.iconClass },
  memory: { icon: ORIGIN_STYLES.memory.Icon, className: ORIGIN_STYLES.memory.iconClass },
  rule: { icon: ORIGIN_STYLES.rule.Icon, className: ORIGIN_STYLES.rule.iconClass },
  merge: { icon: Combine, className: 'text-muted-foreground' },
  auto: { icon: CircleDot, className: 'text-muted-foreground' },
}

/** Lines of one day under its heading, in the order they came. */
function byDay(items: HistoryAction[]): { day: string; date: Date; items: HistoryAction[] }[] {
  const days: { day: string; date: Date; items: HistoryAction[] }[] = []
  for (const item of items) {
    const date = new Date(item.at)
    const day = date.toDateString()
    const last = days[days.length - 1]
    if (last?.day === day) last.items.push(item)
    else days.push({ day, date, items: [item] })
  }
  return days
}

export function HistoryTab() {
  const { t } = useTranslation()
  const dateLocale = useDateLocale()
  const [searchParams] = useSearchParams()
  const linked = searchParams.get('action')
  const [filter, setFilter] = useState<Filter>('all')
  const [open, setOpen] = useState<Set<string>>(() => new Set(linked ? [linked] : []))

  const history = useInfiniteQuery({
    queryKey: ['categorization', 'history', { who: filter }],
    placeholderData: keepPreviousData,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      categorizationApi.history({ who: filter === 'all' ? undefined : filter, limit: PAGE, offset: pageParam }),
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + p.items.length, 0)
      return loaded < last.total ? loaded : undefined
    },
  })
  const items = useMemo(() => history.data?.pages.flatMap((p) => p.items) ?? [], [history.data])
  const days = useMemo(() => byDay(items), [items])

  // The line a toast linked to: in view once it is on screen.
  const scrolled = useRef(false)
  useEffect(() => {
    if (!linked || scrolled.current || !items.some((i) => i.action_id === linked)) return
    scrolled.current = true
    document.getElementById(`history-${linked}`)?.scrollIntoView({ block: 'center' })
  }, [items, linked])

  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const dayLabel = (date: Date) => {
    if (isToday(date)) return t('categorization.history.today')
    if (isYesterday(date)) return t('categorization.history.yesterday')
    return date.toLocaleDateString(dateLocale, { weekday: 'short', day: 'numeric', month: 'short' })
  }

  return (
    <div className="space-y-4" data-testid="history-tab">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Segmented
          value={filter}
          onChange={setFilter}
          testIdPrefix="history-filter"
          options={FILTERS.map((value) => ({ value, label: t(`categorization.history.filter.${value}`) }))}
        />
        <p className="text-xs text-muted-foreground">{t('categorization.history.hint')}</p>
      </div>

      {history.isPending ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : history.isError ? (
        <p className="rounded-xl border border-dashed border-border py-8 text-center text-sm text-muted-foreground">
          {t('categorization.history.error')}
        </p>
      ) : items.length === 0 ? (
        <p className="rounded-xl border border-dashed border-border py-8 text-center text-sm text-muted-foreground">
          {t('categorization.history.empty')}
        </p>
      ) : (
        <div className={cn('space-y-5', history.isPlaceholderData && 'opacity-60')}>
          {days.map(({ day, date, items: lines }) => (
            <section key={day} className="space-y-1.5">
              <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                {dayLabel(date)}
              </h3>
              <ul className="divide-y divide-border rounded-xl border border-border bg-card">
                {lines.map((line) => (
                  <HistoryLine
                    key={line.action_id}
                    line={line}
                    open={open.has(line.action_id)}
                    highlighted={line.action_id === linked}
                    onToggle={() => toggle(line.action_id)}
                  />
                ))}
              </ul>
            </section>
          ))}
          {history.hasNextPage && (
            <div className="flex justify-center">
              <Button
                variant="outline"
                size="sm"
                onClick={() => void history.fetchNextPage()}
                disabled={history.isFetchingNextPage}
              >
                {history.isFetchingNextPage && <Loader2 size={14} className="animate-spin" />}
                {t('categorization.history.loadMore')}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

interface HistoryLineProps {
  line: HistoryAction
  open: boolean
  highlighted: boolean
  onToggle: () => void
}

function HistoryLine({ line, open, highlighted, onToggle }: HistoryLineProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { canWrite } = useWorkspace()
  const dateLocale = useDateLocale()
  const [undoing, setUndoing] = useState(false)

  const time = (iso: string) => new Date(iso).toLocaleTimeString(dateLocale, { hour: '2-digit', minute: '2-digit' })
  const { icon: Icon, className: iconClass } = WHO_ICON[line.who] ?? WHO_ICON.auto
  const categories = (names: (string | null)[], total: number) =>
    total > 1
      ? t('categorization.history.categories', { count: total })
      : (names[0] ?? t('categorization.history.noCategory'))
  const stores =
    line.stores.join(', ') +
    (line.stores_total > line.stores.length
      ? ` ${t('categorization.history.moreStores', { count: line.stores_total - line.stores.length })}`
      : '')

  const undoable = canWrite && line.standing > 0
  const undo = async () => {
    setUndoing(true)
    try {
      const result = await categorizationApi.undo({ action_id: line.action_id })
      if (result.restored > 0) {
        toast.success(t('categorization.correction.undone', { count: result.restored }))
      }
      if (result.skipped > 0) {
        toast.info(t('categorization.history.partial', { count: result.skipped }))
      }
    } catch (err) {
      toast.error(extractApiError(err, t('categorization.correction.undoError')))
    } finally {
      setUndoing(false)
      invalidateCategorizationQueries(queryClient)
    }
  }

  let status: string | null = null
  if (line.undone_at) status = t('categorization.history.undoneAt', { time: time(line.undone_at) })
  else if (line.who === 'merge') status = t('categorization.history.mergeNote')
  else if (line.who !== 'undo' && line.standing === 0) status = t('categorization.history.changedSince')

  return (
    <li
      id={`history-${line.action_id}`}
      data-testid={`history-line-${line.action_id}`}
      className={cn(highlighted && 'bg-primary/5')}
    >
      <div className="flex items-start gap-3 px-3 py-2.5">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-start gap-3 text-left"
        >
          <span className="w-11 shrink-0 pt-0.5 text-xs tabular-nums text-muted-foreground">{time(line.at)}</span>
          <Icon size={15} className={cn('mt-0.5 shrink-0', iconClass)} aria-hidden="true" />
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className={cn('text-sm', line.undone_at && 'text-muted-foreground line-through')}>
              <span className="font-medium">{stores}</span>
              <span className="text-muted-foreground">: </span>
              {categories(line.from_categories, line.from_total)}
              <ArrowRight size={12} className="mx-1 inline align-[-1px] text-muted-foreground" aria-hidden="true" />
              <span className="font-medium">{categories(line.to_categories, line.to_total)}</span>
            </span>
            <span className="text-xs text-muted-foreground">
              {t(`categorization.history.who.${line.who}`)}
              {' · '}
              {t('categorization.history.count', { count: line.count })}
              {status && ` · ${status}`}
            </span>
          </span>
          <ChevronRight
            size={15}
            className={cn('mt-0.5 shrink-0 text-muted-foreground transition-transform', open && 'rotate-90')}
            aria-hidden="true"
          />
        </button>
        {undoable && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 shrink-0 gap-1.5 px-2 text-xs"
            onClick={() => void undo()}
            disabled={undoing}
            data-testid={`history-undo-${line.action_id}`}
          >
            {undoing ? <Loader2 size={13} className="animate-spin" /> : <Undo2 size={13} />}
            {line.standing === line.count
              ? t('categorization.history.undo')
              : t('categorization.history.undoSome', { count: line.standing })}
          </Button>
        )}
      </div>
      {open && <HistoryRows actionId={line.action_id} />}
    </li>
  )
}

function HistoryRows({ actionId }: { actionId: string }) {
  const { t } = useTranslation()
  const { user } = useAuth()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const fallbackCurrency = user?.preferences?.currency_display ?? 'BRL'

  const rows = useInfiniteQuery({
    queryKey: ['categorization', 'history', 'rows', actionId],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => categorizationApi.historyRows(actionId, { limit: ROWS_PAGE, offset: pageParam }),
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + p.items.length, 0)
      return loaded < last.total ? loaded : undefined
    },
  })
  const items = rows.data?.pages.flatMap((p) => p.items) ?? []

  if (rows.isPending) {
    return (
      <div className="px-3 pb-3 pl-[4.5rem]">
        <Skeleton className="h-4 w-2/3" />
      </div>
    )
  }
  if (rows.isError) {
    return <p className="px-3 pb-3 pl-[4.5rem] text-xs text-muted-foreground">{t('categorization.history.error')}</p>
  }
  return (
    <div className="pb-3 pl-[4.5rem] pr-3">
      <ul className="space-y-1 border-l-2 border-border pl-3 text-xs" data-testid={`history-rows-${actionId}`}>
        {items.map((row) => {
          const amount = Number(row.amount) * (row.type === 'debit' ? -1 : 1)
          return (
            <li key={row.event_id} className={cn('flex flex-wrap gap-x-2', row.undone_at && 'text-muted-foreground line-through')}>
              <span className="tabular-nums text-muted-foreground">
                {new Date(`${row.date}T12:00:00`).toLocaleDateString(dateLocale, { day: '2-digit', month: '2-digit' })}
              </span>
              <span className="min-w-0 truncate font-medium">{row.description}</span>
              <span className="tabular-nums">{mask(formatCurrency(amount, row.currency || fallbackCurrency, locale))}</span>
              <span className="text-muted-foreground">
                {row.from_category ?? t('categorization.history.noCategory')}
                {' → '}
                {row.to_category ?? t('categorization.history.noCategory')}
              </span>
            </li>
          )
        })}
      </ul>
      {rows.hasNextPage && (
        <button
          type="button"
          className="mt-1.5 pl-3 text-xs font-medium text-primary hover:underline"
          onClick={() => void rows.fetchNextPage()}
          disabled={rows.isFetchingNextPage}
        >
          {t('categorization.history.loadMore')}
        </button>
      )}
    </div>
  )
}

