/** The review queue: stores the rules, the memory and the AI could not
 *  decide on their own, one row per store, biggest total first.
 *
 *  Built for speed: every answer is one click (or one key), applies to the
 *  whole store and can be undone from the toast. The memory learns each
 *  answer, so this list is meant to shrink over time. When the store has
 *  other rows a person never decided on, the answered row stays in place and
 *  asks whether to file them too (Enter / Escape); otherwise it leaves.
 *  "Ask AI" answers the store from a conversation: its Accept goes through
 *  the same answer (toast with undo, the question, the focus on the row). */
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { CheckCheck, CircleCheck, FolderPlus, Loader2 } from 'lucide-react'
import {
  categories as categoriesApi,
  categoryGroups as categoryGroupsApi,
  categorization as categorizationApi,
} from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateCategorizationQueries, invalidateCategoryQueries } from '@/lib/invalidate-queries'
import { undoWithToast } from '@/lib/categorization-undo'
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
import { Kbd } from '@/components/categorization/kbd'
import type { SimilarPromptHandle } from '@/components/categorization/similar-prompt'
import { useAsk } from '@/components/categorization/ask-provider'
import { storeName } from '@/lib/categorization-review'
import type { AppliedResult, ChooseRequest, NewCategoryProposal, ReviewGroup, ReviewQueue } from '@/types'

export const REVIEW_QUERY_KEY = ['categorization', 'review'] as const
/** The API's page ceiling: the whole queue on one screen in practice. */
const PAGE = 200
const CONFIRM_MS = 4000
/** Parallel requests when accepting everything at once. */
const ACCEPT_ALL_CONCURRENCY = 4
/** How long "Ignore" can be taken back before it reaches the server. */
const DISMISS_UNDO_MS = 6000
const HIDDEN_PROPOSALS_KEY = 'categorization.review.hiddenProposals'

/** A store just answered: kept on screen, where it was, while it asks about
 *  the store's similar rows ("sending" until the answer is in). */
interface Answered {
  group: ReviewGroup
  index: number
  /** A row the answer was applied to; known once the answer is in. */
  referenceId: string | null
  phase: 'sending' | 'asking'
}

/** A row the answer surely went to: the server's applied rows when it says
 *  which; otherwise the first pending row, but only when every pending row
 *  was applied (a row categorized by hand meanwhile is skipped, and the
 *  question would then spread that other category). Null: do not ask. */
function appliedReference(
  group: ReviewGroup,
  result: { applied: number; transaction_ids?: string[] },
): string | null {
  if (result.transaction_ids && result.transaction_ids.length > 0) return result.transaction_ids[0]
  const [first] = group.transaction_ids
  return first && result.applied >= group.transaction_ids.length ? first : null
}

const proposalId = (p: { group: string; name: string }) =>
  `${p.group.trim().toLowerCase()}|${p.name.trim().toLowerCase()}`
const proposalPending = (p: { group: string; name: string }) => `proposal:${proposalId(p)}`
const dismissToastId = (key: string) => `review-dismiss:${key}`

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

function toggleKeys(
  setter: React.Dispatch<React.SetStateAction<Set<string>>>,
  keys: string[],
  on: boolean,
) {
  setter((prev) => {
    const next = new Set(prev)
    for (const key of keys) {
      if (on) next.add(key)
      else next.delete(key)
    }
    return next
  })
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

export function ReviewTab() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { canWrite } = useWorkspace()
  const { user } = useAuth()
  const currency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const ask = useAsk()

  const review = useQuery({
    queryKey: [...REVIEW_QUERY_KEY],
    queryFn: () => categorizationApi.review({ limit: PAGE }),
  })
  // Same keys as the transactions page, so the cache is shared.
  const categoriesQuery = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })
  const categoryGroupsQuery = useQuery({
    queryKey: ['categoryGroups'],
    queryFn: categoryGroupsApi.list,
  })
  const categories = useMemo(() => categoriesQuery.data ?? [], [categoriesQuery.data])
  const categoryGroups = useMemo(() => categoryGroupsQuery.data ?? [], [categoryGroupsQuery.data])

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
  const markPending = useCallback((keys: string[], on: boolean) => toggleKeys(setPending, keys, on), [])
  // Ignored stores wait here while their toast can still take it back; the
  // request goes out when the toast closes, or when the tab goes away.
  const [ignored, setIgnored] = useState<Set<string>>(() => new Set())
  const dismissQueue = useRef(new Map<string, ReviewGroup>())
  // Stores "accept all" is working through: dimmed in place rather than
  // hidden, so the list never flashes "all categorized" before the answer.
  const [applying, setApplying] = useState<Set<string>>(() => new Set())
  const [acceptingAll, setAcceptingAll] = useState(false)
  // Answered stores asking about their similar rows, by merchant key; the
  // server's queue no longer holds them, so the snapshot is what is shown.
  const [answered, setAnswered] = useState<Map<string, Answered>>(() => new Map())
  const promptHandles = useRef(new Map<string, SimilarPromptHandle>())

  const [hiddenProposals, setHiddenProposals] = useState(readHiddenProposals)
  const [openKey, setOpenKey] = useState<string | null>(null)
  // The focused store, by key (the index is the fallback once it leaves):
  // a row leaving above it must not move the roving tabindex off it.
  const [focus, setFocus] = useState<{ key: string | null; idx: number }>({ key: null, idx: 0 })
  const rowRefs = useRef<(HTMLLIElement | null)[]>([])
  const rootRef = useRef<HTMLDivElement>(null)
  // Set by an answer: once the row leaves, the next one takes the focus.
  const refocus = useRef(false)
  const pointerDown = useRef(false)
  const legendId = useId()

  const data = review.data
  const visible = useMemo(() => {
    const rows = (data?.groups ?? []).filter(
      (g) => !pending.has(g.merchant_key) && !ignored.has(g.merchant_key) && !answered.has(g.merchant_key),
    )
    // Each answered store goes back where it was (or last, if the list shrank).
    for (const a of [...answered.values()].sort((x, y) => x.index - y.index)) {
      rows.splice(Math.min(a.index, rows.length), 0, a.group)
    }
    return rows
  }, [data, pending, ignored, answered])
  const visibleRef = useRef(visible)
  // Before the refocus below: a row focused there reads the new list.
  useLayoutEffect(() => {
    visibleRef.current = visible
  })
  const setFocusIdx = useCallback(
    (idx: number) => setFocus({ key: visibleRef.current[idx]?.merchant_key ?? null, idx }),
    [],
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
    () =>
      visible.filter(
        (g) =>
          !applying.has(g.merchant_key) &&
          !answered.has(g.merchant_key) &&
          !!g.suggestion?.category_id &&
          catalog.has(g.suggestion.category_id),
      ),
    [visible, catalog, applying, answered],
  )
  const focusedAt = focus.key ? visible.findIndex((g) => g.merchant_key === focus.key) : -1
  const current = focusedAt >= 0 ? focusedAt : Math.max(0, Math.min(focus.idx, visible.length - 1))

  useLayoutEffect(() => {
    if (!refocus.current) return
    refocus.current = false
    // The last store gone: the tab keeps the focus, not the page's body.
    const el = rowRefs.current[current]
    if (el) el.focus()
    else rootRef.current?.focus({ preventScroll: true })
  })

  const invalidateReview = useCallback(
    () => queryClient.invalidateQueries({ queryKey: [...REVIEW_QUERY_KEY] }),
    [queryClient],
  )

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
    (eventIds: string[]) => undoWithToast(eventIds, { t, queryClient }),
    [queryClient, t],
  )

  const appliedToast = useCallback(
    (applied: number, eventIds: string[], title?: string, onUndo?: () => void) => {
      const message = t('categorization.review.applied', { count: applied })
      const action =
        eventIds.length > 0
          ? {
              label: t('common.undo'),
              onClick: () => {
                onUndo?.()
                void undo(eventIds)
              },
            }
          : undefined
      if (title) toast.success(title, { description: message, action, duration: 8000 })
      else toast.success(message, { action, duration: 8000 })
    },
    [t, undo],
  )

  /** The answered store leaves; if it held the focus, the next store (now
   *  at the same index) takes it, so the next answer is one key away. */
  const releaseAnswered = useCallback((key: string) => {
    const idx = visibleRef.current.findIndex((g) => g.merchant_key === key)
    const active = document.activeElement as HTMLElement | null
    const activeRow = active?.closest<HTMLElement>('[data-review-row]')
    const heldFocus =
      idx >= 0 && (!active || active === document.body || Number(activeRow?.dataset.index) === idx)
    if (heldFocus) {
      // By index: the store that takes this one's place.
      setFocus({ key: null, idx })
      refocus.current = true
    }
    promptHandles.current.delete(key)
    setAnswered((prev) => {
      if (!prev.has(key)) return prev
      const next = new Map(prev)
      next.delete(key)
      return next
    })
  }, [])

  /** One answer for one store: the row stays where it is while it is sent,
   *  then the toast with undo and the question about the store's similar
   *  rows (the row leaves when there are none); on failure the row comes
   *  back as it was. `done` runs on success and may return the toast's title.
   *  `proposal`: the store's new-category proposal this answer accepts. */
  const answerStore = useCallback(
    async <R extends { applied: number; event_ids: string[]; transaction_ids?: string[] }>(
      group: ReviewGroup,
      send: () => Promise<R>,
      done?: (result: R) => string | undefined,
      proposal?: NewCategoryProposal,
    ) => {
      if (!canWrite) return
      const key = group.merchant_key
      const shown = visibleRef.current
      const at = shown.findIndex((g) => g.merchant_key === key)
      setAnswered((prev) =>
        new Map(prev).set(key, {
          group,
          index: at >= 0 ? at : shown.length,
          referenceId: null,
          phase: 'sending',
        }),
      )
      if (proposal) markPending([proposalPending(proposal)], true)
      setOpenKey((open) => (open === key ? null : open))
      // A refetch already on its way may still hold this store.
      await queryClient.cancelQueries({ queryKey: [...REVIEW_QUERY_KEY] })
      try {
        const result = await send()
        dropFromCache([key], proposal)
        // Undoing the answer takes the question with it.
        appliedToast(result.applied, result.event_ids, done?.(result), () => releaseAnswered(key))
        invalidateCategorizationQueries(queryClient)
        const referenceId = result.applied > 0 ? appliedReference(group, result) : null
        if (referenceId) {
          setAnswered((prev) => {
            const entry = prev.get(key)
            return entry ? new Map(prev).set(key, { ...entry, referenceId, phase: 'asking' }) : prev
          })
        } else {
          releaseAnswered(key)
        }
      } catch (err) {
        toast.error(extractApiError(err, t('common.error')))
        void invalidateReview()
        releaseAnswered(key)
      } finally {
        if (proposal) markPending([proposalPending(proposal)], false)
      }
    },
    [appliedToast, canWrite, dropFromCache, invalidateReview, markPending, queryClient, releaseAnswered, t],
  )

  const accept = useCallback(
    (group: ReviewGroup, categoryId: string) =>
      answerStore(group, () =>
        categorizationApi.accept({
          merchant_key: group.merchant_key,
          category_id: categoryId,
          transaction_ids: group.transaction_ids,
        }),
      ),
    [answerStore],
  )

  /** "Other…" beyond an existing category (a new one, made on the spot):
   *  over the store's pending rows as they are now (no `transaction_ids`),
   *  so a row categorized meanwhile is left alone. */
  const choose = useCallback(
    (group: ReviewGroup, payload: Omit<ChooseRequest, 'merchant_key'>) =>
      answerStore(
        group,
        () => categorizationApi.choose({ merchant_key: group.merchant_key, ...payload }),
        (result) => {
          if (result.created_category || result.created_group) invalidateCategoryQueries(queryClient)
          return result.created_category ? t('categorization.create.created') : undefined
        },
      ),
    [answerStore, queryClient, t],
  )

  /** A new category for one store (its row's proposal, or a proposal that
   *  covers only it): one store's choice, so it asks about the similar rows
   *  like any other answer. */
  const createForStore = useCallback(
    (group: ReviewGroup, draft: { group: string; name: string } | null, proposal?: NewCategoryProposal) => {
      if (!draft) return
      void answerStore(
        group,
        () =>
          categorizationApi.createCategory({
            group: draft.group,
            name: draft.name,
            merchant_keys: [group.merchant_key],
          }),
        () => {
          invalidateCategoryQueries(queryClient)
          return t('categorization.review.created', { path: `${draft.group} › ${draft.name}` })
        },
        proposal,
      )
    },
    [answerStore, queryClient, t],
  )

  /** The conversation is closing: the store's row takes the focus back. If
   *  it left meanwhile (rows skipped, nothing similar: it may go during the
   *  dialog's exit, while the focus is still in there), the row now at its
   *  place does, or the last one; with no rows left, the list's card. */
  const focusRow = useCallback((key: string, at: number) => {
    const shown = visibleRef.current
    if (shown.length === 0) {
      rootRef.current?.focus({ preventScroll: true })
      return
    }
    const found = shown.findIndex((g) => g.merchant_key === key)
    const idx = found >= 0 ? found : Math.max(0, Math.min(at, shown.length - 1))
    setFocus({ key: shown[idx].merchant_key, idx })
    rowRefs.current[idx]?.focus()
  }, [])

  /** "Ask AI" about one store, over its pending rows; the choice made there
   *  is this store's answer like any other (`answerStore`). */
  const askAbout = useCallback(
    (group: ReviewGroup) => {
      const key = group.merchant_key
      const at = Math.max(0, visibleRef.current.findIndex((g) => g.merchant_key === key))
      const guessId = group.suggestion?.category_id
      const lastDate = group.transactions.reduce<string | null>(
        (latest, tx) => (!latest || tx.date > latest ? tx.date : latest),
        null,
      )
      ask.open({
        merchantKey: key,
        // An unnamed Pix (a hashed key) is just "Pix" in the title.
        storeName: storeName(group) ?? t('categorization.review.pix'),
        count: group.count,
        total: Number(group.total),
        currency,
        lastDate,
        samples: group.samples.slice(0, 3),
        suggestedGroupId: (guessId && catalog.get(guessId)?.category.group_id) || null,
        onAccepted: (result) =>
          void answerStore(
            group,
            async () => result,
            // The dialog already refreshed the categories it created.
            (r) => t(r.created_category ? 'categorization.create.created' : 'categorization.ask.accepted'),
          ),
        restoreFocus: () => focusRow(key, at),
      })
    },
    [answerStore, ask, catalog, currency, focusRow, t],
  )
  const askBlockedReason = ask.blocked ? t(`categorization.ask.blocked.${ask.blocked}`) : null

  /** Send a queued "Ignore"; a no-op once undone or already sent. */
  const commitDismiss = useCallback(
    async (key: string) => {
      if (!dismissQueue.current.delete(key)) return
      try {
        await categorizationApi.dismiss(key)
        dropFromCache([key])
        invalidateCategorizationQueries(queryClient)
      } catch (err) {
        toast.error(extractApiError(err, t('common.error')))
        void invalidateReview()
      } finally {
        toggleKeys(setIgnored, [key], false)
      }
    },
    [dropFromCache, invalidateReview, queryClient, t],
  )

  // "Ignore" sticks on the server (until the store shows up again), so it
  // waits for its toast: undo only shows the row again, nothing is sent.
  const dismiss = useCallback(
    (group: ReviewGroup) => {
      if (!canWrite) return
      const key = group.merchant_key
      dismissQueue.current.set(key, group)
      toggleKeys(setIgnored, [key], true)
      setOpenKey((open) => (open === key ? null : open))
      toast.success(
        t('categorization.review.dismissed', {
          name: storeName(group) ?? t('categorization.review.pix'),
        }),
        {
          id: dismissToastId(key),
          duration: DISMISS_UNDO_MS,
          action: {
            label: t('common.undo'),
            onClick: () => {
              dismissQueue.current.delete(key)
              toggleKeys(setIgnored, [key], false)
            },
          },
          onAutoClose: () => void commitDismiss(key),
          onDismiss: () => void commitDismiss(key),
        },
      )
    },
    [canWrite, commitDismiss, t],
  )

  // Leaving the tab (or the page) sends whatever is still waiting.
  useEffect(() => {
    const queue = dismissQueue.current
    const flush = () => {
      const keys = [...queue.keys()]
      queue.clear()
      if (keys.length === 0) return
      for (const key of keys) toast.dismiss(dismissToastId(key))
      // keepalive: a closing tab would otherwise drop the request.
      void Promise.allSettled(
        keys.map((key) => categorizationApi.dismiss(key, { keepalive: true })),
      ).then(() => invalidateCategorizationQueries(queryClient))
    }
    window.addEventListener('pagehide', flush)
    return () => {
      window.removeEventListener('pagehide', flush)
      flush()
    }
  }, [queryClient])

  const createCategory = useCallback(
    async (proposal: NewCategoryProposal) => {
      if (!canWrite) return
      // A proposal for one store is that store's choice: it asks too.
      if (proposal.merchant_keys.length === 1) {
        const [only] = proposal.merchant_keys
        const group = visibleRef.current.find((g) => g.merchant_key === only)
        if (group && !answered.has(only)) {
          createForStore(group, proposal, proposal)
          return
        }
      }
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
        void invalidateReview()
      } finally {
        markPending(keys, false)
      }
    },
    [answered, appliedToast, canWrite, createForStore, dropFromCache, invalidateReview, markPending, queryClient, t],
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
    setAcceptingAll(true)
    toggleKeys(setApplying, keys, true)
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
    const failures: unknown[] = []
    results.forEach((r, i) => {
      if (r.status === 'fulfilled') {
        done.push(r.value)
        doneKeys.push(batch[i].merchant_key)
      } else failures.push(r.reason)
    })
    dropFromCache(doneKeys)
    toggleKeys(setApplying, keys, false)
    setAcceptingAll(false)
    if (done.length > 0) {
      appliedToast(
        done.reduce((sum, r) => sum + r.applied, 0),
        done.flatMap((r) => r.event_ids),
      )
    }
    if (failures.length > 0) {
      toast.error(
        t('categorization.review.partialFailure', {
          count: failures.length,
          error: extractApiError(failures[0], t('common.error')),
        }),
      )
    }
    invalidateCategorizationQueries(queryClient)
  }

  /** After an answer the next store (now at the same index) takes the focus,
   *  by key or by click, so the next answer is one key press away. */
  const focusAfter = (idx: number) => {
    setFocus({ key: null, idx })
    refocus.current = true
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

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        moveFocus(idx + 1)
        return
      case 'ArrowUp':
        e.preventDefault()
        moveFocus(idx - 1)
        return
      case 'Home':
        e.preventDefault()
        moveFocus(0)
        return
      case 'End':
        e.preventDefault()
        moveFocus(visible.length - 1)
        return
    }

    // An answered store asks about its similar rows: Enter applies, Escape
    // declines; its other shortcuts went away with its buttons.
    if (answered.has(group.merchant_key)) {
      const prompt = promptHandles.current.get(group.merchant_key)
      if (e.key === 'Enter') {
        e.preventDefault()
        prompt?.apply()
      } else if (e.key === 'Escape') {
        e.preventDefault()
        prompt?.decline()
      }
      return
    }

    const answer = (run: () => void) => {
      e.preventDefault()
      if (applying.has(group.merchant_key)) return
      focusAfter(idx)
      run()
    }
    const suggestion = group.suggestion
    switch (e.key) {
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
        if (canWrite) answer(() => dismiss(group))
        break
    }
  }

  /** Tabbing into the list lands on the current row rather than on the
   *  first button; clicks and the picker's popover keep their own focus. */
  const onListFocus = (e: React.FocusEvent<HTMLUListElement>) => {
    const viaPointer = pointerDown.current
    pointerDown.current = false
    if (viaPointer) return
    const target = e.target as HTMLElement
    if (!e.currentTarget.contains(target) || target.hasAttribute('data-review-row')) return
    const from = e.relatedTarget as HTMLElement | null
    if (from && (e.currentTarget.contains(from) || from.closest('[data-radix-popper-content-wrapper]'))) {
      return
    }
    rowRefs.current[current]?.focus()
  }

  // Waits for the categories too, so the suggestion buttons do not pop in.
  if (review.isPending || categoriesQuery.isPending || categoryGroupsQuery.isPending) {
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

  const hiddenCount = data.groups.filter(
    (g) => pending.has(g.merchant_key) || ignored.has(g.merchant_key) || answered.has(g.merchant_key),
  ).length
  const remaining = Math.max(0, data.total - hiddenCount)
  // Answered stores still on screen are not waiting for review any more.
  const reviewing = visible.length - answered.size
  const paged = data.total > data.groups.length
  const showShortcuts = canWrite && visible.length > 0
  const money = (value: number | string) => mask(formatCurrency(Number(value), currency, locale))

  return (
    // Focusable only by script: where the focus lands when no row is left.
    <div data-testid="categorization-review" ref={rootRef} tabIndex={-1} className="outline-none">
      <SectionCard>
        <SectionHeader
          title={t('categorization.review.title', { count: remaining })}
          description={visible.length > 0 ? t('categorization.review.hint') : undefined}
          action={
            canWrite && (acceptingAll || acceptable.length > 0) ? (
              <Button
                size="sm"
                variant={armed ? 'default' : 'outline'}
                className="h-8 gap-1.5"
                disabled={acceptingAll}
                onClick={() => void acceptAll()}
              >
                {acceptingAll ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <CheckCheck size={14} />
                )}
                {acceptingAll
                  ? t('categorization.review.applying')
                  : armed
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
            onFocus={onListFocus}
            onPointerDownCapture={() => {
              pointerDown.current = true
            }}
          >
            {visible.map((group, i) => {
              const answer = answered.get(group.merchant_key)
              return (
                <ReviewGroupRow
                  key={group.merchant_key}
                  group={group}
                  answer={answer ? { phase: answer.phase, referenceId: answer.referenceId ?? '' } : undefined}
                  onAnswerDone={() => releaseAnswered(group.merchant_key)}
                  promptHandle={(handle) => {
                    if (handle) promptHandles.current.set(group.merchant_key, handle)
                    else promptHandles.current.delete(group.merchant_key)
                  }}
                  index={i}
                  catalog={catalog}
                  categories={categories}
                  categoryGroups={categoryGroups}
                  canWrite={canWrite}
                  open={openKey === group.merchant_key}
                  current={i === current}
                  busy={applying.has(group.merchant_key)}
                  describedBy={showShortcuts ? legendId : undefined}
                  currency={currency}
                  offerNewCategory={!proposedKeys.has(group.merchant_key)}
                  rowRef={(el) => {
                    rowRefs.current[i] = el
                  }}
                  onFocusRow={() => setFocusIdx(i)}
                  onToggle={() =>
                    setOpenKey((open) => (open === group.merchant_key ? null : group.merchant_key))
                  }
                  onAccept={(categoryId) => {
                    focusAfter(i)
                    void accept(group, categoryId)
                  }}
                  onChoose={(payload) => {
                    focusAfter(i)
                    void choose(group, payload)
                  }}
                  onDismiss={() => {
                    focusAfter(i)
                    dismiss(group)
                  }}
                  onCreateCategory={() => {
                    focusAfter(i)
                    createForStore(group, group.new_category)
                  }}
                  onAsk={ask.available ? () => askAbout(group) : undefined}
                  askBlockedReason={askBlockedReason}
                />
              )
            })}
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
              <p
                id={legendId}
                className="hidden flex-wrap items-center gap-1.5 sm:flex"
                data-testid="review-shortcuts"
              >
                <span>{t('categorization.review.keysHint')}</span>
                <span aria-hidden="true">·</span>
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
                  shown: reviewing,
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
