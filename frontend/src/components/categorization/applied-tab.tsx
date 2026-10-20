/** The audit screen: everything the AI, the memory and the rules categorized
 *  on their own, newest day first, one line per transaction showing what it
 *  was before and what it is now. Fix a row in place (the memory learns it)
 *  or undo one, a store, a batch or a selection (they go back to Review). */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ChevronRight, ListChecks, Loader2, Undo2, X, type LucideIcon } from 'lucide-react'
import {
  categories as categoriesApi,
  categoryGroups as categoryGroupsApi,
  categorization as categorizationApi,
  transactions as transactionsApi,
} from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateCategorizationQueries } from '@/lib/invalidate-queries'
import { groupApplied, isToday, isYesterday, type StoreBucket } from '@/lib/categorization-applied'
import { useWorkspace } from '@/contexts/workspace-context'
import { useAuth } from '@/contexts/auth-context'
import { useSidebarState } from '@/contexts/sidebar-state-context'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { SectionCard, SectionHeader, Segmented } from '@/components/invoice-ui'
import { CategoryIcon } from '@/components/category-icon'
import { CategorySelect } from '@/components/category-select'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import { formatCurrency } from '@/lib/format'
import { ORIGIN_STYLES } from '@/lib/categorization-origins'
import { cn } from '@/lib/utils'
import type { AppliedEvent, Category, CategoryGroup, CategorySource } from '@/types'

const PAGE = 50
/** Stores with this many rows or fewer start open; bigger ones start closed. */
const OPEN_BY_DEFAULT = 3
/** Confidence cut-off shared with the auto-apply threshold. */
const CUT = 90

type SourceFilter = 'ai' | 'memory' | 'rule'
type ConfidenceFilter = 'all' | 'low' | 'high'

const SOURCES: SourceFilter[] = ['ai', 'memory', 'rule']

const ORIGIN: Record<string, { icon: LucideIcon; className: string }> = Object.fromEntries(
  (['ai', 'memory', 'rule'] as const).map((s) => [s, { icon: ORIGIN_STYLES[s].Icon, className: ORIGIN_STYLES[s].iconClass }]),
)

/** Undoing these puts the row back in Review as a pending suggestion. */
const BACK_TO_REVIEW: CategorySource[] = ['ai', 'memory']

interface CategoryEntry {
  category: Category
  groupName: string | null
}

export function AppliedTab() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { canWrite } = useWorkspace()
  const { user } = useAuth()
  const { collapsed: sidebarCollapsed } = useSidebarState()
  const currency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()

  const [sources, setSources] = useState<Set<SourceFilter>>(() => new Set())
  const [confidence, setConfidence] = useState<ConfidenceFilter>('all')
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [opened, setOpened] = useState<Map<string, boolean>>(() => new Map())
  const [fixed, setFixed] = useState<Set<string>>(() => new Set())
  const [busy, setBusy] = useState(false)

  const sourceParam = [...sources].sort().join(',') || undefined
  const applied = useInfiniteQuery({
    queryKey: ['categorization', 'applied', { source: sourceParam ?? null, confidence }],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      categorizationApi.applied({
        source: sourceParam,
        min_confidence: confidence === 'high' ? CUT : undefined,
        max_confidence: confidence === 'low' ? CUT - 1 : undefined,
        limit: PAGE,
        offset: pageParam,
      }),
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((sum, p) => sum + p.items.length, 0)
      return last.items.length > 0 && loaded < last.total ? loaded : undefined
    },
  })
  // Same keys as the transactions page, so the cache is shared.
  const categoriesQuery = useQuery({ queryKey: ['categories'], queryFn: categoriesApi.list })
  const categoryGroupsQuery = useQuery({ queryKey: ['categoryGroups'], queryFn: categoryGroupsApi.list })
  const categories = useMemo(() => categoriesQuery.data ?? [], [categoriesQuery.data])
  const categoryGroups = useMemo(() => categoryGroupsQuery.data ?? [], [categoryGroupsQuery.data])

  const catalog = useMemo(() => {
    const groupName = new Map(categoryGroups.map((g) => [g.id, g.name]))
    const map = new Map<string, CategoryEntry>()
    for (const category of categories) {
      map.set(category.id, {
        category,
        groupName: category.group_id ? groupName.get(category.group_id) ?? null : null,
      })
    }
    return map
  }, [categories, categoryGroups])

  const pages = applied.data?.pages
  const total = pages?.[pages.length - 1]?.total ?? 0
  const events = useMemo(
    () => (pages ?? []).flatMap((p) => p.items).filter((e) => !fixed.has(e.event_id)),
    [pages, fixed],
  )
  const days = useMemo(() => groupApplied(events), [events])
  const byId = useMemo(() => new Map(events.map((e) => [e.event_id, e])), [events])
  const selectedEvents = useMemo(
    () => [...selected].map((id) => byId.get(id)).filter((e): e is AppliedEvent => !!e),
    [selected, byId],
  )

  const money = (value: number | string, cur = currency) =>
    mask(formatCurrency(Number(value), cur, locale))
  const path = (id: string | null) => {
    const entry = id ? catalog.get(id) : undefined
    if (!entry) return null
    return entry.groupName ? `${entry.groupName} › ${entry.category.name}` : entry.category.name
  }

  const toggleSource = (source: SourceFilter) =>
    setSources((prev) => {
      const next = new Set(prev)
      if (next.has(source)) next.delete(source)
      else next.add(source)
      return next
    })

  const toggleIds = useCallback((ids: string[], on: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev)
      for (const id of ids) {
        if (on) next.add(id)
        else next.delete(id)
      }
      return next
    })
  }, [])

  const undo = useCallback(
    async (eventIds: string[]) => {
      if (!canWrite || busy || eventIds.length === 0) return
      const involved = eventIds.map((id) => byId.get(id)).filter((e): e is AppliedEvent => !!e)
      setBusy(true)
      try {
        const result = await categorizationApi.undo({ event_ids: eventIds })
        const backToReview = involved.some((e) => BACK_TO_REVIEW.includes(e.source))
        const skipped = result.skipped > 0 ? t('categorization.applied.skipped', { count: result.skipped }) : undefined
        if (result.restored > 0) {
          toast.success(
            t(backToReview ? 'categorization.applied.undone' : 'categorization.applied.undoneRule', {
              count: result.restored,
            }),
            { description: skipped },
          )
        } else if (skipped) {
          toast.info(skipped)
        }
        setSelected(new Set())
      } catch (err) {
        toast.error(extractApiError(err, t('categorization.applied.undoError')))
      } finally {
        setBusy(false)
        invalidateCategorizationQueries(queryClient)
      }
    },
    [busy, byId, canWrite, queryClient, t],
  )

  const correct = useCallback(
    async (event: AppliedEvent, categoryId: string) => {
      if (!canWrite || !categoryId || categoryId === event.new_category_id) return
      try {
        await transactionsApi.update(event.transaction.id, { category_id: categoryId })
        setFixed((prev) => new Set(prev).add(event.event_id))
        setSelected((prev) => {
          const next = new Set(prev)
          next.delete(event.event_id)
          return next
        })
        toast.success(t('categorization.applied.corrected'))
        invalidateCategorizationQueries(queryClient)
      } catch (err) {
        toast.error(extractApiError(err, t('categorization.applied.correctError')))
      }
    },
    [canWrite, queryClient, t],
  )

  const dayLabel = (date: Date) =>
    isToday(date)
      ? t('categorization.applied.today')
      : isYesterday(date)
        ? t('categorization.applied.yesterday')
        : date.toLocaleDateString(dateLocale, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })

  const selectedNet = selectedEvents.reduce((sum, e) => sum + Number(e.transaction.amount), 0)

  const filters = (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-4 py-3 sm:px-5">
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={t('categorization.applied.filters.source')}>
        {SOURCES.map((source) => {
          const on = sources.has(source)
          const { icon: Icon, className } = ORIGIN[source]
          return (
            <button
              key={source}
              type="button"
              aria-pressed={on}
              data-testid={`applied-source-${source}`}
              onClick={() => toggleSource(source)}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors',
                on
                  ? 'border-primary/40 bg-primary/10 text-foreground'
                  : 'border-border text-muted-foreground hover:text-foreground',
              )}
            >
              <Icon size={13} className={className} />
              {t(`categorization.applied.filters.${source}`)}
            </button>
          )
        })}
      </div>
      <Segmented<ConfidenceFilter>
        value={confidence}
        onChange={setConfidence}
        testIdPrefix="applied-confidence"
        options={[
          { value: 'all', label: t('categorization.applied.filters.all') },
          { value: 'low', label: t('categorization.applied.filters.low') },
          { value: 'high', label: t('categorization.applied.filters.high') },
        ]}
      />
    </div>
  )

  if (applied.isPending || categoriesQuery.isPending || categoryGroupsQuery.isPending) {
    return (
      <div data-testid="categorization-applied">
        <SectionCard>
          <div className="border-b border-border px-4 py-4 sm:px-5">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="mt-2 h-3 w-72 max-w-full" />
          </div>
          <div className="divide-y divide-border" aria-busy="true">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="flex items-center justify-between gap-4 px-4 py-3 sm:px-5">
                <div className="min-w-0 flex-1 space-y-1.5">
                  <Skeleton className="h-4 w-44" />
                  <Skeleton className="h-3 w-56 max-w-full" />
                </div>
                <Skeleton className="hidden h-6 w-48 sm:block" />
              </div>
            ))}
          </div>
        </SectionCard>
      </div>
    )
  }

  if (applied.isError) {
    return (
      <div data-testid="categorization-applied">
        <SectionCard>
          <div className="py-10 text-center">
            <p className="text-sm text-muted-foreground">{t('categorization.applied.loadError')}</p>
            <Button variant="outline" size="sm" className="mt-3 h-8" onClick={() => applied.refetch()}>
              {t('common.retry')}
            </Button>
          </div>
        </SectionCard>
      </div>
    )
  }

  const filtered = sources.size > 0 || confidence !== 'all'

  return (
    <div data-testid="categorization-applied" className={cn(selected.size > 0 && canWrite && 'pb-24')}>
      <SectionCard>
        <SectionHeader
          title={t('categorization.applied.title', { count: total })}
          description={t('categorization.applied.hint')}
        />
        {filters}

        {days.length === 0 ? (
          <div className="py-10 text-center">
            <ListChecks size={20} className="mx-auto mb-2 text-muted-foreground" />
            <p className="text-sm font-medium text-foreground">
              {filtered ? t('categorization.applied.emptyFiltered') : t('categorization.applied.empty')}
            </p>
            {!filtered && (
              <p className="mt-0.5 text-xs text-muted-foreground">{t('categorization.applied.emptyHint')}</p>
            )}
          </div>
        ) : (
          <div aria-label={t('categorization.applied.listLabel')}>
            {days.map((day) => (
              <section key={day.day} className="border-b border-border last:border-b-0">
                <div className="flex flex-wrap items-center justify-between gap-2 bg-muted/40 px-4 py-2 sm:px-5">
                  <h3 className="text-xs font-semibold text-foreground">
                    {dayLabel(day.date)}
                    <span className="ml-2 font-normal text-muted-foreground">{day.count}</span>
                  </h3>
                  {canWrite && day.runs.length > 0 && (
                    <BatchUndo
                      runs={day.runs.map((run) => ({
                        id: run.runId,
                        count: run.eventIds.length,
                        time: run.at.toLocaleTimeString(dateLocale, { hour: '2-digit', minute: '2-digit' }),
                        eventIds: run.eventIds,
                      }))}
                      busy={busy}
                      onUndo={(ids) => void undo(ids)}
                    />
                  )}
                </div>
                <div className="divide-y divide-border">
                  {day.stores.map((store) => (
                    <StoreGroup
                      key={store.key}
                      store={store}
                      open={opened.get(store.key) ?? store.events.length <= OPEN_BY_DEFAULT}
                      onToggle={() =>
                        setOpened((prev) =>
                          new Map(prev).set(
                            store.key,
                            !(prev.get(store.key) ?? store.events.length <= OPEN_BY_DEFAULT),
                          ),
                        )
                      }
                      canWrite={canWrite}
                      selected={selected}
                      onSelect={toggleIds}
                      catalog={catalog}
                      categories={categories}
                      categoryGroups={categoryGroups}
                      path={path}
                      money={money}
                      onCorrect={correct}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}

        {(applied.hasNextPage || total > events.length) && days.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-3 text-xs text-muted-foreground sm:px-5">
            <p>{t('categorization.applied.showing', { shown: events.length, total })}</p>
            {applied.hasNextPage && (
              <Button
                variant="outline"
                size="sm"
                className="h-8"
                disabled={applied.isFetchingNextPage}
                onClick={() => void applied.fetchNextPage()}
              >
                {applied.isFetchingNextPage && <Loader2 size={14} className="mr-1.5 animate-spin" />}
                {t('categorization.applied.loadMore')}
              </Button>
            )}
          </div>
        )}
      </SectionCard>

      {canWrite && selected.size > 0 && (
        <div
          className={cn(
            'fixed bottom-0 left-0 right-0 z-50',
            'transition-[left] duration-300 ease-in-out motion-reduce:transition-none',
            sidebarCollapsed ? 'lg:left-16' : 'lg:left-60',
          )}
        >
          <div className="mx-auto max-w-7xl px-3 pb-4 md:px-6 md:pb-6">
            <div className="flex items-center gap-1.5 rounded-2xl border border-border bg-card p-2 shadow-xl">
              <div className="flex items-center gap-2.5 whitespace-nowrap pl-3 pr-2 sm:pr-4">
                <span className="inline-flex size-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
                  {selected.size}
                </span>
                <div className="hidden flex-col leading-tight sm:flex">
                  <span className="text-[11px] font-medium text-muted-foreground">
                    {t('categorization.applied.selected')}
                  </span>
                  <span
                    className={cn(
                      'text-sm font-bold tabular-nums',
                      selectedNet >= 0 ? 'text-emerald-600' : 'text-rose-500',
                    )}
                  >
                    {mask(
                      `${selectedNet >= 0 ? '+' : '−'}${formatCurrency(Math.abs(selectedNet), currency, locale)}`,
                    )}
                  </span>
                </div>
              </div>
              <div className="ml-auto flex items-center gap-1.5">
                <Button
                  size="sm"
                  className="h-9 gap-1.5"
                  disabled={busy}
                  onClick={() => void undo(selectedEvents.map((e) => e.event_id))}
                >
                  {busy ? <Loader2 size={14} className="animate-spin" /> : <Undo2 size={14} />}
                  {t('categorization.applied.undoSelected')}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-9 gap-1.5 text-muted-foreground"
                  onClick={() => setSelected(new Set())}
                >
                  <X size={14} />
                  {t('categorization.applied.clear')}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

interface BatchRun {
  id: string
  count: number
  time: string
  eventIds: string[]
}

/** One button per batch of the day; more than two fold into a menu. The
 *  undo covers exactly the rows listed, and the label says how many. */
function BatchUndo({ runs, busy, onUndo }: { runs: BatchRun[]; busy: boolean; onUndo: (ids: string[]) => void }) {
  const { t } = useTranslation()
  const label = (run: BatchRun) =>
    runs.length > 1
      ? t('categorization.applied.undoBatchAt', { time: run.time, count: run.count })
      : t('categorization.applied.undoBatch', { count: run.count })
  const cls = 'h-7 gap-1.5 px-2 text-xs text-muted-foreground'
  if (runs.length > 2) {
    return (
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="sm" className={cls} disabled={busy}>
            <Undo2 size={13} />
            {t('categorization.applied.undoBatchMore')}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {runs.map((run) => (
            <DropdownMenuItem key={run.id} onSelect={() => onUndo(run.eventIds)}>
              {label(run)}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    )
  }
  return (
    <div className="flex flex-wrap items-center gap-1">
      {runs.map((run) => (
        <Button key={run.id} variant="ghost" size="sm" className={cls} disabled={busy} onClick={() => onUndo(run.eventIds)}>
          <Undo2 size={13} />
          {label(run)}
        </Button>
      ))}
    </div>
  )
}

interface StoreGroupProps {
  store: StoreBucket
  open: boolean
  onToggle: () => void
  canWrite: boolean
  selected: Set<string>
  onSelect: (ids: string[], on: boolean) => void
  catalog: Map<string, CategoryEntry>
  categories: Category[]
  categoryGroups: CategoryGroup[]
  path: (id: string | null) => string | null
  money: (value: number | string, currency?: string) => string
  onCorrect: (event: AppliedEvent, categoryId: string) => void
}

function StoreGroup({
  store, open, onToggle, canWrite, selected, onSelect, catalog, categories, categoryGroups, path, money, onCorrect,
}: StoreGroupProps) {
  const { t } = useTranslation()
  const ids = store.events.map((e) => e.event_id)
  const picked = ids.filter((id) => selected.has(id)).length
  const checkRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (checkRef.current) checkRef.current.indeterminate = picked > 0 && picked < ids.length
  }, [picked, ids.length])
  const after = store.categoryId ? catalog.get(store.categoryId) : undefined

  return (
    <div>
      <div className="flex items-center gap-2 px-4 py-2.5 sm:px-5">
        {canWrite && (
          <input
            ref={checkRef}
            type="checkbox"
            className="size-4 shrink-0 accent-primary"
            checked={picked === ids.length}
            aria-label={t('categorization.applied.selectRow', { name: store.name })}
            onChange={(e) => onSelect(ids, e.target.checked)}
          />
        )}
        <button
          type="button"
          aria-expanded={open}
          onClick={onToggle}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <ChevronRight
            size={14}
            className={cn('shrink-0 text-muted-foreground transition-transform', open && 'rotate-90')}
          />
          <span className="truncate text-sm font-medium text-foreground">{store.name}</span>
          <span className="shrink-0 text-xs text-muted-foreground">· {store.events.length}</span>
          <span className="shrink-0 text-xs text-muted-foreground" aria-hidden="true">→</span>
          {after && (
            <CategoryIcon icon={after.category.icon} color={after.category.color} size="sm" />
          )}
          <span className="truncate text-xs text-foreground">
            {path(store.categoryId) ?? t('categorization.applied.noCategory')}
          </span>
        </button>
      </div>
      {open && (
        <ul className="bg-muted/20">
          {store.events.map((event) => (
            <AppliedRow
              key={event.event_id}
              event={event}
              canWrite={canWrite}
              checked={selected.has(event.event_id)}
              onCheck={(on) => onSelect([event.event_id], on)}
              catalog={catalog}
              categories={categories}
              categoryGroups={categoryGroups}
              money={money}
              onCorrect={onCorrect}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

interface AppliedRowProps {
  event: AppliedEvent
  canWrite: boolean
  checked: boolean
  onCheck: (on: boolean) => void
  catalog: Map<string, CategoryEntry>
  categories: Category[]
  categoryGroups: CategoryGroup[]
  money: (value: number | string, currency?: string) => string
  onCorrect: (event: AppliedEvent, categoryId: string) => void
}

function AppliedRow({ event, canWrite, checked, onCheck, catalog, categories, categoryGroups, money, onCorrect }: AppliedRowProps) {
  const { t } = useTranslation()
  const before = event.previous_category_id ? catalog.get(event.previous_category_id) : undefined
  const after = event.new_category_id ? catalog.get(event.new_category_id) : undefined
  const origin = ORIGIN[event.source] ?? ORIGIN.rule
  const OriginIcon = origin.icon
  const tx = event.transaction

  return (
    <li className="flex flex-col gap-2 px-4 py-2.5 sm:flex-row sm:items-center sm:gap-4 sm:pl-12 sm:pr-5">
      <div className="flex min-w-0 flex-1 items-start gap-2.5">
        {canWrite && (
          <input
            type="checkbox"
            className="mt-0.5 size-4 shrink-0 accent-primary"
            checked={checked}
            aria-label={t('categorization.applied.selectRow', { name: tx.description })}
            onChange={(e) => onCheck(e.target.checked)}
          />
        )}
        <div className="min-w-0">
          <p className="truncate text-sm text-foreground" title={`${t('categorization.applied.original')}: ${tx.description}`}>
            {tx.description}
          </p>
          <p className="flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
            <OriginIcon size={12} className={origin.className} aria-label={t(`categorization.applied.filters.${event.source === 'ai' || event.source === 'memory' ? event.source : 'rule'}`)} />
            {event.confidence != null && (
              <span>{t('categorization.applied.confidence', { value: event.confidence })}</span>
            )}
            {event.reason && <span className="min-w-0 truncate">{event.reason}</span>}
          </p>
        </div>
      </div>

      <div className="flex items-center gap-2 pl-6 sm:pl-0">
        <span className="inline-flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
          {before && <CategoryIcon icon={before.category.icon} color={before.category.color} size="sm" />}
          <span className={cn(!before && 'italic')}>
            {before ? before.category.name : t('categorization.applied.noCategory')}
          </span>
        </span>
        <span className="text-xs text-muted-foreground" aria-hidden="true">→</span>
        {canWrite ? (
          <CategorySelect
            value={event.new_category_id ?? ''}
            onChange={(id) => onCorrect(event, id)}
            categories={categories}
            groups={categoryGroups}
            currentCategory={after?.category ?? null}
            className="h-8 w-40 px-2 text-xs"
            contentProps={{ className: 'w-72 p-0 overflow-hidden' }}
          />
        ) : (
          <span className="inline-flex items-center gap-1.5 text-xs text-foreground">
            {after && <CategoryIcon icon={after.category.icon} color={after.category.color} size="sm" />}
            {after ? after.category.name : t('categorization.applied.noCategory')}
          </span>
        )}
        <span className="ml-auto w-24 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
          {money(tx.amount, tx.currency)}
        </span>
      </div>
    </li>
  )
}
