/** The review queue: stores the rules, the memory and the AI could not
 *  decide on their own, one row per store, biggest total first.
 *
 *  Built for speed: every answer is one click (or one key), applies to the
 *  whole store, disappears at once and can be undone from the toast. The
 *  memory learns each answer, so this list is meant to shrink over time. */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { CheckCheck, CircleCheck, FolderPlus } from 'lucide-react'
import {
  categories as categoriesApi,
  categoryGroups as categoryGroupsApi,
  categorization as categorizationApi,
} from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateCategorizationQueries, invalidateCategoryQueries } from '@/lib/invalidate-queries'
import { useWorkspace } from '@/contexts/workspace-context'
import { useAuth } from '@/contexts/auth-context'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { SectionCard, SectionHeader } from '@/components/invoice-ui'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { formatCurrency } from '@/lib/format'
import { cn } from '@/lib/utils'
import { ReviewGroupRow, type CategoryInfo } from '@/components/categorization/review-group-row'
import { storeName } from '@/lib/categorization-review'
import type { AppliedResult, NewCategoryProposal, ReviewGroup, ReviewQueue } from '@/types'

export const REVIEW_QUERY_KEY = ['categorization', 'review'] as const
/** The API's page ceiling: the whole queue on one screen in practice. */
const PAGE = 200
const CONFIRM_MS = 4000
/** Parallel requests when accepting everything at once. */
const ACCEPT_ALL_CONCURRENCY = 4
const HIDDEN_PROPOSALS_KEY = 'categorization.review.hiddenProposals'

const proposalId = (p: { group: string; name: string }) =>
  `${p.group.trim().toLowerCase()}|${p.name.trim().toLowerCase()}`
const proposalPending = (p: { group: string; name: string }) => `proposal:${proposalId(p)}`

function readHiddenProposals(): Set<string> {
  try {
    const raw = sessionStorage.getItem(HIDDEN_PROPOSALS_KEY)
    return new Set(raw ? (JSON.parse(raw) as string[]) : [])
  } catch {
    return new Set()
  }
}

function writeHiddenProposals(ids: Set<string>) {
  try {
    sessionStorage.setItem(HIDDEN_PROPOSALS_KEY, JSON.stringify([...ids]))
  } catch {
    // Private mode or blocked storage: hidden for this render tree only.
  }
}

/** `Promise.allSettled` with at most `limit` requests in flight. */
async function settleLimited<T, R>(
  items: T[],
  limit: number,
  run: (item: T) => Promise<R>,
): Promise<PromiseSettledResult<R>[]> {
  const results: PromiseSettledResult<R>[] = new Array(items.length)
  let next = 0
  async function worker() {
    while (next < items.length) {
      const i = next++
      try {
        results[i] = { status: 'fulfilled', value: await run(items[i]) }
      } catch (reason) {
        results[i] = { status: 'rejected', reason }
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker))
  return results
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex h-4 min-w-4 items-center justify-center rounded border border-border bg-muted px-1 font-mono text-[10px] font-medium leading-none text-muted-foreground">
      {children}
    </kbd>
  )
}

export function ReviewTab() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { canWrite } = useWorkspace()
  const { user } = useAuth()
  const currency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()

  const review = useQuery({
    queryKey: [...REVIEW_QUERY_KEY],
    queryFn: () => categorizationApi.review({ limit: PAGE }),
  })
  // Same keys as the transactions page, so the cache is shared.
  const { data: categories = [] } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })
  const { data: categoryGroups = [] } = useQuery({
    queryKey: ['categoryGroups'],
    queryFn: categoryGroupsApi.list,
  })

  const catalog = useMemo(() => {
    const groupName = new Map(categoryGroups.map((g) => [g.id, g.name]))
    const map = new Map<string, CategoryInfo>()
    for (const category of categories) {
      map.set(category.id, {
        category,
        groupName: category.group_id ? groupName.get(category.group_id) ?? null : null,
      })
    }
    return map
  }, [categories, categoryGroups])

  // Stores (and proposals) with an answer in flight: hidden right away, so
  // the list moves at the speed of the clicks, and shown again on failure.
  const [pending, setPending] = useState<Set<string>>(() => new Set())
  const markPending = useCallback((keys: string[], on: boolean) => {
    setPending((prev) => {
      const next = new Set(prev)
      for (const key of keys) {
        if (on) next.add(key)
        else next.delete(key)
      }
      return next
    })
  }, [])

  const [hiddenProposals, setHiddenProposals] = useState(readHiddenProposals)
  const [openKey, setOpenKey] = useState<string | null>(null)
  const [focusIdx, setFocusIdx] = useState(0)
  const rowRefs = useRef<(HTMLLIElement | null)[]>([])
  // Set by a keyboard answer: once the row leaves, the next one takes focus.
  const refocus = useRef(false)

  const data = review.data
  const visible = useMemo(
    () => (data?.groups ?? []).filter((g) => !pending.has(g.merchant_key)),
    [data, pending],
  )
  const proposals = useMemo(
    () =>
      (data?.new_category_proposals ?? []).filter(
        (p) => !hiddenProposals.has(proposalId(p)) && !pending.has(proposalPending(p)),
      ),
    [data, hiddenProposals, pending],
  )
  const proposedKeys = useMemo(
    () => new Set(proposals.flatMap((p) => p.merchant_keys)),
    [proposals],
  )
  const acceptable = useMemo(
    () => visible.filter((g) => g.suggestion?.category_id && catalog.has(g.suggestion.category_id)),
    [visible, catalog],
  )
  const current = Math.max(0, Math.min(focusIdx, visible.length - 1))

  useLayoutEffect(() => {
    if (!refocus.current) return
    refocus.current = false
    rowRefs.current[current]?.focus()
  })

  /** Drop answered stores from the cached queue; the refetch confirms. */
  const dropFromCache = useCallback(
    (keys: string[], proposal?: NewCategoryProposal) => {
      const gone = new Set(keys)
      queryClient.setQueryData<ReviewQueue>([...REVIEW_QUERY_KEY], (old) => {
        if (!old) return old
        const groups = old.groups.filter((g) => !gone.has(g.merchant_key))
        return {
          ...old,
          groups,
          total: Math.max(0, old.total - (old.groups.length - groups.length)),
          new_category_proposals: old.new_category_proposals.filter(
            (p) => !proposal || proposalId(p) !== proposalId(proposal),
          ),
        }
      })
    },
    [queryClient],
  )

  const undo = useCallback(
    async (eventIds: string[]) => {
      try {
        await categorizationApi.undo({ event_ids: eventIds })
        toast.success(t('categorization.review.undone'))
      } catch (err) {
        toast.error(extractApiError(err, t('common.error')))
      } finally {
        invalidateCategorizationQueries(queryClient)
      }
    },
    [queryClient, t],
  )

  const appliedToast = useCallback(
    (applied: number, eventIds: string[], title?: string) => {
      const message = t('categorization.review.applied', { count: applied })
      const action =
        eventIds.length > 0
          ? { label: t('common.undo'), onClick: () => void undo(eventIds) }
          : undefined
      if (title) toast.success(title, { description: message, action, duration: 8000 })
      else toast.success(message, { action, duration: 8000 })
    },
    [t, undo],
  )

  const accept = useCallback(
    async (group: ReviewGroup, categoryId: string) => {
      if (!canWrite) return
      const key = group.merchant_key
      markPending([key], true)
      setOpenKey((open) => (open === key ? null : open))
      // A refetch already on its way may still hold this store.
      await queryClient.cancelQueries({ queryKey: [...REVIEW_QUERY_KEY] })
      try {
        const result = await categorizationApi.accept({
          merchant_key: key,
          category_id: categoryId,
          transaction_ids: group.transaction_ids,
        })
        dropFromCache([key])
        appliedToast(result.applied, result.event_ids)
        invalidateCategorizationQueries(queryClient)
      } catch (err) {
        toast.error(extractApiError(err, t('common.error')))
      } finally {
        markPending([key], false)
      }
    },
    [appliedToast, canWrite, dropFromCache, markPending, queryClient, t],
  )

  const dismiss = useCallback(
    async (group: ReviewGroup) => {
      if (!canWrite) return
      const key = group.merchant_key
      markPending([key], true)
      await queryClient.cancelQueries({ queryKey: [...REVIEW_QUERY_KEY] })
      try {
        await categorizationApi.dismiss(key)
        dropFromCache([key])
        toast.success(
          t('categorization.review.dismissed', {
            name: storeName(group) ?? t('categorization.review.pix'),
          }),
        )
        invalidateCategorizationQueries(queryClient)
      } catch (err) {
        toast.error(extractApiError(err, t('common.error')))
      } finally {
        markPending([key], false)
      }
    },
    [canWrite, dropFromCache, markPending, queryClient, t],
  )

  const createCategory = useCallback(
    async (proposal: NewCategoryProposal) => {
      if (!canWrite) return
      const keys = [...proposal.merchant_keys, proposalPending(proposal)]
      markPending(keys, true)
      await queryClient.cancelQueries({ queryKey: [...REVIEW_QUERY_KEY] })
      try {
        const result = await categorizationApi.createCategory({
          group: proposal.group,
          name: proposal.name,
          merchant_keys: proposal.merchant_keys,
        })
        dropFromCache(proposal.merchant_keys, proposal)
        appliedToast(
          result.applied,
          result.event_ids,
          t('categorization.review.created', { path: `${proposal.group} › ${proposal.name}` }),
        )
        invalidateCategoryQueries(queryClient)
        invalidateCategorizationQueries(queryClient)
      } catch (err) {
        toast.error(extractApiError(err, t('common.error')))
      } finally {
        markPending(keys, false)
      }
    },
    [appliedToast, canWrite, dropFromCache, markPending, queryClient, t],
  )

  // "Accept all" asks twice: the first click arms it for a few seconds.
  const [armed, setArmed] = useState(false)
  const armTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (armTimer.current) clearTimeout(armTimer.current)
  }, [])

  const acceptAll = async () => {
    if (!armed) {
      setArmed(true)
      if (armTimer.current) clearTimeout(armTimer.current)
      armTimer.current = setTimeout(() => setArmed(false), CONFIRM_MS)
      return
    }
    if (armTimer.current) clearTimeout(armTimer.current)
    setArmed(false)

    const batch = acceptable.slice()
    const keys = batch.map((g) => g.merchant_key)
    markPending(keys, true)
    await queryClient.cancelQueries({ queryKey: [...REVIEW_QUERY_KEY] })
    const results = await settleLimited(batch, ACCEPT_ALL_CONCURRENCY, (g) =>
      categorizationApi.accept({
        merchant_key: g.merchant_key,
        category_id: g.suggestion!.category_id!,
        transaction_ids: g.transaction_ids,
      }),
    )
    const done: AppliedResult[] = []
    const doneKeys: string[] = []
    let failure: unknown = null
    results.forEach((r, i) => {
      if (r.status === 'fulfilled') {
        done.push(r.value)
        doneKeys.push(batch[i].merchant_key)
      } else failure ??= r.reason
    })
    dropFromCache(doneKeys)
    markPending(keys, false)
    if (done.length > 0) {
      appliedToast(
        done.reduce((sum, r) => sum + r.applied, 0),
        done.flatMap((r) => r.event_ids),
      )
    }
    if (failure) toast.error(extractApiError(failure, t('common.error')))
    invalidateCategorizationQueries(queryClient)
  }

  const moveFocus = (idx: number) => {
    if (visible.length === 0) return
    const target = Math.max(0, Math.min(idx, visible.length - 1))
    setFocusIdx(target)
    rowRefs.current[target]?.focus()
    rowRefs.current[target]?.scrollIntoView?.({ block: 'nearest' })
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLUListElement>) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return
    const target = e.target as HTMLElement
    // Only the row itself: a key typed in the picker's search box, or
    // pressed on a button inside the row, belongs to that control.
    const rowEl = target.closest<HTMLElement>('[data-review-row]')
    if (!rowEl || rowEl !== target) return
    const idx = Number(rowEl.dataset.index)
    const group = visible[idx]
    if (!group) return

    const answer = (run: () => void) => {
      e.preventDefault()
      setFocusIdx(idx)
      refocus.current = true
      run()
    }
    const suggestion = group.suggestion
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        moveFocus(idx + 1)
        break
      case 'ArrowUp':
        e.preventDefault()
        moveFocus(idx - 1)
        break
      case 'Home':
        e.preventDefault()
        moveFocus(0)
        break
      case 'End':
        e.preventDefault()
        moveFocus(visible.length - 1)
        break
      case 'Enter':
        e.preventDefault()
        setOpenKey((open) => (open === group.merchant_key ? null : group.merchant_key))
        break
      case '1':
        if (canWrite && suggestion?.category_id && catalog.has(suggestion.category_id)) {
          const id = suggestion.category_id
          answer(() => void accept(group, id))
        }
        break
      case '2':
        if (canWrite && suggestion?.alt_category_id && catalog.has(suggestion.alt_category_id)) {
          const id = suggestion.alt_category_id
          answer(() => void accept(group, id))
        }
        break
      case 'x':
      case 'X':
        if (canWrite) answer(() => void dismiss(group))
        break
    }
  }

  if (review.isPending) {
    return (
      <div data-testid="categorization-review">
        <SectionCard>
          <div className="border-b border-border px-4 py-4 sm:px-5">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="mt-2 h-3 w-64 max-w-full" />
          </div>
          <div className="divide-y divide-border" aria-busy="true">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="flex items-center justify-between gap-4 px-4 py-3 sm:px-5">
                <div className="min-w-0 flex-1 space-y-1.5">
                  <Skeleton className="h-4 w-36" />
                  <Skeleton className="h-3 w-48 max-w-full" />
                </div>
                <Skeleton className="hidden h-8 w-64 sm:block" />
              </div>
            ))}
          </div>
        </SectionCard>
      </div>
    )
  }

  if (review.isError || !data) {
    return (
      <div data-testid="categorization-review">
        <SectionCard>
          <div className="py-10 text-center">
            <p className="text-sm text-muted-foreground">{t('categorization.review.loadError')}</p>
            <Button variant="outline" size="sm" className="mt-3 h-8" onClick={() => review.refetch()}>
              {t('common.retry')}
            </Button>
          </div>
        </SectionCard>
      </div>
    )
  }

  const hiddenCount = data.groups.length - visible.length
  const remaining = Math.max(0, data.total - hiddenCount)
  const paged = data.total > data.groups.length
  const showShortcuts = canWrite && visible.length > 0
  const money = (value: number | string) => mask(formatCurrency(Number(value), currency, locale))

  return (
    <div data-testid="categorization-review">
      <SectionCard>
        <SectionHeader
          title={t('categorization.review.title', { count: remaining })}
          description={visible.length > 0 ? t('categorization.review.hint') : undefined}
          action={
            canWrite && acceptable.length > 0 ? (
              <Button
                size="sm"
                variant={armed ? 'default' : 'outline'}
                className="h-8 gap-1.5"
                onClick={() => void acceptAll()}
              >
                <CheckCheck size={14} />
                {armed
                  ? t('categorization.review.confirmAll', { count: acceptable.length })
                  : t('categorization.review.acceptAll')}
              </Button>
            ) : undefined
          }
        />

        {canWrite && proposals.length > 0 && (
          <div className="divide-y divide-violet-200/60 border-b border-border bg-violet-50/60 dark:divide-violet-900/40 dark:bg-violet-950/20">
            {proposals.map((p) => (
              <div
                key={proposalId(p)}
                className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4 sm:px-5"
              >
                <div className="flex min-w-0 items-start gap-2">
                  <FolderPlus size={15} className="mt-0.5 shrink-0 text-violet-600 dark:text-violet-400" />
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-foreground">
                      {t('categorization.review.proposal', { path: `${p.group} › ${p.name}` })}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {t('categorization.review.txCount', { count: p.count })}
                      {' · '}
                      <span className="tabular-nums">{money(p.total)}</span>
                    </p>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-1.5 pl-6 sm:flex sm:shrink-0 sm:pl-0">
                  <Button size="sm" className="h-8" onClick={() => void createCategory(p)}>
                    {t('categorization.review.createAndApply')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-8 text-muted-foreground"
                    onClick={() =>
                      setHiddenProposals((prev) => {
                        const next = new Set(prev).add(proposalId(p))
                        writeHiddenProposals(next)
                        return next
                      })
                    }
                  >
                    {t('categorization.review.notNow')}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}

        {visible.length === 0 ? (
          <div className="py-10 text-center">
            <CircleCheck size={20} className="mx-auto mb-2 text-emerald-500" />
            <p className="text-sm font-medium text-foreground">{t('categorization.review.empty')}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">{t('categorization.review.emptyHint')}</p>
          </div>
        ) : (
          <ul
            className="divide-y divide-border"
            aria-label={t('categorization.review.listLabel')}
            onKeyDown={onKeyDown}
          >
            {visible.map((group, i) => (
              <ReviewGroupRow
                key={group.merchant_key}
                group={group}
                index={i}
                catalog={catalog}
                categories={categories}
                categoryGroups={categoryGroups}
                canWrite={canWrite}
                open={openKey === group.merchant_key}
                current={i === current}
                currency={currency}
                offerNewCategory={!proposedKeys.has(group.merchant_key)}
                rowRef={(el) => {
                  rowRefs.current[i] = el
                }}
                onFocusRow={() => setFocusIdx(i)}
                onToggle={() =>
                  setOpenKey((open) => (open === group.merchant_key ? null : group.merchant_key))
                }
                onAccept={(categoryId) => void accept(group, categoryId)}
                onDismiss={() => void dismiss(group)}
                onCreateCategory={() =>
                  group.new_category &&
                  void createCategory({
                    ...group.new_category,
                    merchant_keys: [group.merchant_key],
                    count: group.count,
                    total: group.total,
                  })
                }
              />
            ))}
          </ul>
        )}

        {(paged || showShortcuts) && (
          <div
            className={cn(
              'flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-border px-4 py-2 text-[11px] text-muted-foreground sm:px-5',
              // The legend is for keyboards: on a phone the bar holds only the page note.
              paged ? 'flex' : 'hidden sm:flex',
            )}
          >
            {showShortcuts ? (
              <p className="hidden items-center gap-1.5 sm:flex" data-testid="review-shortcuts">
                <Kbd>1</Kbd>/<Kbd>2</Kbd> {t('categorization.review.keys.accept')}
                <span aria-hidden="true">·</span>
                <Kbd>↑</Kbd><Kbd>↓</Kbd> {t('categorization.review.keys.navigate')}
                <span aria-hidden="true">·</span>
                <Kbd>Enter</Kbd> {t('categorization.review.keys.expand')}
                <span aria-hidden="true">·</span>
                <Kbd>x</Kbd> {t('categorization.review.keys.ignore')}
              </p>
            ) : (
              <span />
            )}
            {paged && (
              <p>
                {t('categorization.review.showing', {
                  shown: visible.length,
                  total: remaining,
                })}
              </p>
            )}
          </div>
        )}
      </SectionCard>
    </div>
  )
}
