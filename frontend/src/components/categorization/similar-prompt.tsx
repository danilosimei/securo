/** "Apply to the N similar ones from this store too?" — asked after every
 *  manual choice in the Categorization section.
 *
 *  The N are the store's other rows with a different category that nobody
 *  decided by hand (rows a rule filed stay out too). Nothing changes without
 *  the click; "Apply to N" files exactly the rows the question counted and
 *  its toast can undo it. With nothing to ask about, it renders nothing and
 *  calls `onDone` at once.
 *
 *  The question is announced (a polite live region that is there from the
 *  start, so the change is heard) and carries `questionId`, for a host whose
 *  focus stays elsewhere to point `aria-describedby` at it. */
import { useCallback, useEffect, useId, useImperativeHandle, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { CheckCheck, Loader2 } from 'lucide-react'
import { categorization as categorizationApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateCategorizationQueries } from '@/lib/invalidate-queries'
import { undoWithToast } from '@/lib/categorization-undo'
import { useWorkspace } from '@/contexts/workspace-context'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { Button } from '@/components/ui/button'
import { formatCurrency } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Kbd } from '@/components/categorization/kbd'
import type { SimilarAppliedResult, SimilarTransaction } from '@/types'

/** Long enough to read the result and reach for "Undo". */
const TOAST_MS = 8000
/** The apply endpoint takes at most this many rows per call. */
const APPLY_CHUNK = 5000
/** "Show which" lists this many rows; the rest are only counted. */
export const SIMILAR_PREVIEW = 50

/** What a host that owns the keyboard (the review row) can trigger. */
export interface SimilarPromptHandle {
  apply: () => void
  decline: () => void
}

interface SimilarPromptProps {
  /** A row the choice was just applied to: the store and the category. */
  referenceId: string
  storeName: string
  /** For a row that carries no currency of its own. */
  currency: string
  /** Answered, declined, or nothing to ask: the host lets the prompt go. */
  onDone: () => void
  /** "At <store>" before the question, where the store's row is gone. */
  showStore?: boolean
  /** Shown while the similar rows are looked up. */
  loading?: React.ReactNode
  /** Show the Enter / Esc key caps (the host routes those keys). */
  shortcuts?: boolean
  /** For a roving tabindex: -1 keeps the buttons out of the Tab order. */
  tabIndex?: number
  /** Take the focus when the question shows up and nothing else holds it. */
  autoFocus?: boolean
  /** The id of the announced question, for the host's `aria-describedby`. */
  questionId?: string
  handle?: React.Ref<SimilarPromptHandle>
  className?: string
}

export function SimilarPrompt({
  referenceId,
  storeName,
  currency,
  onDone,
  showStore = false,
  loading,
  shortcuts = false,
  tabIndex,
  autoFocus = false,
  questionId,
  handle,
  className,
}: SimilarPromptProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { canWrite } = useWorkspace()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const listId = useId()
  const ownQuestionId = useId()

  // Asked once: a refetch while the person reads would move the N under them.
  const query = useQuery({
    queryKey: ['categorizationSimilar', referenceId],
    queryFn: () => categorizationApi.similar(referenceId, { excludeRule: true }),
    enabled: canWrite,
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  })
  const data = query.data
  const [open, setOpen] = useState(false)
  const [applying, setApplying] = useState(false)
  const applyRef = useRef<HTMLButtonElement>(null)

  const onDoneRef = useRef(onDone)
  useEffect(() => {
    onDoneRef.current = onDone
  })
  const finished = useRef(false)
  const finish = useCallback(() => {
    if (finished.current) return
    finished.current = true
    onDoneRef.current()
  }, [])

  // Nothing to ask (or no way to answer): out of the way at once. A failed
  // lookup is not worth an error: the choice itself went through.
  const nothing = !canWrite || query.isError || data?.count === 0
  useEffect(() => {
    if (nothing) finish()
  }, [nothing, finish])

  const asking = !!data && data.count > 0 && canWrite
  useEffect(() => {
    if (!asking || !autoFocus) return
    const active = document.activeElement
    // No scroll: a bar the page does not jump to.
    // Lost focus, or parked by the host to wait for this bar.
    if (!active || active === document.body || active.hasAttribute('data-focus-parking')) {
      applyRef.current?.focus({ preventScroll: true })
    }
  }, [asking, autoFocus])

  const undo = (eventIds: string[]) => undoWithToast(eventIds, { t, queryClient })

  /** How many were updated, and how many of the shown ones had changed
   *  meanwhile (left as they were); none updated is a notice, not a success. */
  const appliedToast = (applied: number, eventIds: string[], skipped: number) => {
    const title =
      t('categorization.similar.applied', { count: applied }) +
      (skipped > 0 ? t('categorization.similar.skipped', { count: skipped }) : '')
    const options = {
      duration: TOAST_MS,
      action:
        eventIds.length > 0 ? { label: t('common.undo'), onClick: () => void undo(eventIds) } : undefined,
    }
    if (applied === 0 && skipped > 0) toast.info(title, options)
    else toast.success(title, options)
  }

  const apply = async () => {
    if (!data || data.count === 0 || applying || finished.current) return
    setApplying(true)
    const ids = data.transaction_ids
    const done: SimilarAppliedResult[] = []
    try {
      for (let i = 0; i < ids.length; i += APPLY_CHUNK) {
        done.push(
          await categorizationApi.applySimilar(referenceId, {
            excludeRule: true,
            transactionIds: ids.slice(i, i + APPLY_CHUNK),
          }),
        )
      }
    } catch (err) {
      toast.error(extractApiError(err, t('common.error')))
      if (done.length === 0) {
        setApplying(false)
        return
      }
    }
    // Some blocks may have gone through before a failure: those still get
    // their toast and their undo, and the question (now stale) is over.
    appliedToast(
      done.reduce((sum, r) => sum + r.applied, 0),
      done.flatMap((r) => r.event_ids),
      // An older server does not say.
      done.reduce((sum, r) => sum + (r.skipped?.length ?? 0), 0),
    )
    invalidateCategorizationQueries(queryClient)
    finish()
  }

  const decline = () => {
    if (!applying) finish()
  }

  useImperativeHandle(handle, () => ({ apply: () => void apply(), decline }))

  if (nothing || (!asking && !query.isPending)) return null

  const question = asking ? t('categorization.similar.ask', { count: data.count }) : ''
  const storeLabel = t('categorization.similar.store', { store: storeName })
  const applyLabel = asking ? t('categorization.similar.apply', { count: data.count }) : ''
  const noLabel = t('categorization.similar.no')
  const showDate = (iso: string) =>
    new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString(dateLocale)
  const amount = (tx: SimilarTransaction) =>
    `${tx.type === 'credit' ? '+' : '−'}${formatCurrency(Math.abs(Number(tx.amount)), tx.currency || currency, locale)}`
  const preview = asking ? data.transactions.slice(0, SIMILAR_PREVIEW) : []
  const more = asking ? data.count - preview.length : 0

  return (
    <div
      data-similar-prompt={asking ? '' : undefined}
      role={asking ? 'group' : undefined}
      aria-labelledby={asking ? questionId ?? ownQuestionId : undefined}
      className={cn(asking && cn('min-w-0', className))}
      onKeyDown={(e) => {
        if (!asking || e.key !== 'Escape' || e.altKey || e.ctrlKey || e.metaKey) return
        e.preventDefault()
        e.stopPropagation()
        decline()
      }}
    >
      {!asking && loading}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <p className="min-w-0 text-xs text-foreground">
          {/* There from the first render, empty: filling it is what is announced. */}
          <span id={questionId ?? ownQuestionId} role="status" aria-live="polite" aria-atomic="true">
            {asking && (
              <>
                {showStore && (
                  <>
                    <span className="font-semibold">{storeLabel}</span>
                    <span className="text-muted-foreground"> · </span>
                  </>
                )}
                <span>{question}</span>
              </>
            )}
          </span>
        </p>
        {asking && (
          <div className="flex flex-wrap items-center gap-1.5">
            <Button
              ref={applyRef}
              size="xs"
              className="h-7 gap-1.5 px-2.5"
              disabled={applying}
              tabIndex={tabIndex}
              aria-label={applyLabel}
              aria-keyshortcuts={shortcuts ? 'Enter' : undefined}
              onClick={() => void apply()}
            >
              {applying ? <Loader2 className="animate-spin" /> : <CheckCheck />}
              {applyLabel}
              {shortcuts && (
                <Kbd
                  aria-hidden="true"
                  className="ml-0.5 hidden border-primary-foreground/30 bg-primary-foreground/15 text-primary-foreground sm:inline-flex"
                >
                  Enter
                </Kbd>
              )}
            </Button>
            <Button
              variant="ghost"
              size="xs"
              className="h-7 px-2"
              tabIndex={tabIndex}
              aria-expanded={open}
              aria-controls={open ? listId : undefined}
              onClick={() => setOpen((v) => !v)}
            >
              {open ? t('categorization.similar.hide') : t('categorization.similar.show')}
            </Button>
            <Button
              variant="ghost"
              size="xs"
              className="h-7 gap-1.5 px-2 text-muted-foreground"
              disabled={applying}
              tabIndex={tabIndex}
              aria-label={noLabel}
              aria-keyshortcuts={shortcuts ? 'Escape' : undefined}
              onClick={decline}
            >
              {noLabel}
              {shortcuts && (
                <Kbd aria-hidden="true" className="hidden sm:inline-flex">
                  Esc
                </Kbd>
              )}
            </Button>
          </div>
        )}
      </div>
      {asking && open && (
        <div id={listId} className="mt-2">
          <ul className="max-h-60 divide-y divide-border overflow-y-auto rounded-lg border border-border bg-muted/30 text-xs">
            {preview.map((tx) => (
              <li key={tx.id} className="flex items-center gap-3 px-3 py-1.5">
                <span className="w-20 shrink-0 tabular-nums text-muted-foreground">{showDate(tx.date)}</span>
                <span className="min-w-0 flex-1 truncate text-foreground" title={tx.description}>
                  {tx.description}
                </span>
                <span
                  className={cn(
                    'shrink-0 font-medium tabular-nums',
                    tx.type === 'credit' ? 'text-emerald-600 dark:text-emerald-400' : 'text-foreground',
                  )}
                >
                  {mask(amount(tx))}
                </span>
              </li>
            ))}
          </ul>
          {more > 0 && (
            <p className="mt-1.5 text-xs text-muted-foreground">
              {t('categorization.review.more', { count: more })}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
