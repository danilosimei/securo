/** "Apply to the N similar ones from this store too?" — asked after every
 *  manual choice in the Categorization section.
 *
 *  The N are the store's other rows with a different category that nobody
 *  decided by hand (rows a rule filed stay out too). Nothing changes without
 *  the click; "Apply to N" files exactly the rows the question counted (the
 *  ones "Show which" lists) and its toast can undo it. With nothing to ask
 *  about, it renders nothing and calls `onDone` at once. */
import { useCallback, useEffect, useId, useImperativeHandle, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { CheckCheck, Loader2 } from 'lucide-react'
import { categorization as categorizationApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateCategorizationQueries } from '@/lib/invalidate-queries'
import { useWorkspace } from '@/contexts/workspace-context'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { Button } from '@/components/ui/button'
import { formatCurrency } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Kbd } from '@/components/categorization/kbd'
import type { SimilarTransaction } from '@/types'

/** Long enough to read the result and reach for "Undo". */
const TOAST_MS = 8000

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
    if (!active || active === document.body) applyRef.current?.focus()
  }, [asking, autoFocus])

  const undo = async (eventIds: string[]) => {
    try {
      await categorizationApi.undo({ event_ids: eventIds })
      toast.success(t('categorization.review.undone'))
    } catch (err) {
      toast.error(extractApiError(err, t('common.error')))
    } finally {
      invalidateCategorizationQueries(queryClient)
    }
  }

  const apply = async () => {
    if (!data || data.count === 0 || applying || finished.current) return
    setApplying(true)
    try {
      const result = await categorizationApi.applySimilar(referenceId, {
        excludeRule: true,
        transactionIds: data.transaction_ids,
      })
      const eventIds = result.event_ids
      toast.success(t('categorization.similar.applied', { count: result.applied }), {
        duration: TOAST_MS,
        action:
          eventIds.length > 0
            ? { label: t('common.undo'), onClick: () => void undo(eventIds) }
            : undefined,
      })
      invalidateCategorizationQueries(queryClient)
      finish()
    } catch (err) {
      toast.error(extractApiError(err, t('common.error')))
      setApplying(false)
    }
  }

  const decline = () => {
    if (!applying) finish()
  }

  useImperativeHandle(handle, () => ({ apply: () => void apply(), decline }))

  if (!asking) return query.isPending && canWrite ? <>{loading}</> : null

  const question = t('categorization.similar.ask', { count: data.count })
  const applyLabel = t('categorization.similar.apply', { count: data.count })
  const noLabel = t('categorization.similar.no')
  const showDate = (iso: string) =>
    new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString(dateLocale)
  const amount = (tx: SimilarTransaction) =>
    `${tx.type === 'credit' ? '+' : '−'}${formatCurrency(Math.abs(Number(tx.amount)), tx.currency || currency, locale)}`

  return (
    <div
      data-similar-prompt=""
      role="group"
      aria-label={showStore ? `${t('categorization.similar.store', { store: storeName })}: ${question}` : question}
      className={cn('min-w-0', className)}
      onKeyDown={(e) => {
        if (e.key !== 'Escape' || e.altKey || e.ctrlKey || e.metaKey) return
        e.preventDefault()
        e.stopPropagation()
        decline()
      }}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <p className="min-w-0 text-xs text-foreground">
          {showStore && (
            <>
              <span className="font-semibold">{t('categorization.similar.store', { store: storeName })}</span>
              <span aria-hidden="true" className="text-muted-foreground"> · </span>
            </>
          )}
          <span>{question}</span>
        </p>
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
      </div>
      {open && (
        <ul
          id={listId}
          className="mt-2 max-h-60 divide-y divide-border overflow-y-auto rounded-lg border border-border bg-muted/30 text-xs"
        >
          {data.transactions.map((tx) => (
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
      )}
    </div>
  )
}
