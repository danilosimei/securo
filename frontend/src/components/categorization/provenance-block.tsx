/** "How it got here", inside the transaction dialog: who put the current
 *  category on the row (AI, memory, a rule, the bank, the person), how sure
 *  and why, what the row was originally, and a one-click undo. */
import { useContext, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ChevronDown, CircleDot, Loader2, Undo2, UserRound } from 'lucide-react'

import { categorization as categorizationApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { ORIGIN_STYLES } from '@/lib/categorization-origins'
import { invalidateCategorizationQueries } from '@/lib/invalidate-queries'
import { formatRelativeFromNow } from '@/lib/relative-time'
import { cn } from '@/lib/utils'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { useDateLocale } from '@/hooks/use-display-locale'
import { Button } from '@/components/ui/button'
import type { Category, CategorySource, ProvenanceEvent } from '@/types'

/** Origins worth explaining even when no event was recorded. */
const EXPLAINED: CategorySource[] = ['ai', 'memory', 'rule', 'provider']
/** Origins that open in one line: there is little to tell. */
const COLLAPSED: CategorySource[] = ['rule', 'provider']
/** Undoing these puts the row back in Review. */
const BACK_TO_REVIEW: CategorySource[] = ['ai', 'memory']
const LABELLED: CategorySource[] = ['ai', 'memory', 'rule', 'provider', 'user', 'none']

interface ProvenanceBlockProps {
  transactionId: string
  /** To name the category the row had before. */
  categories: Category[]
  /** After an undo, with the category the row went back to. */
  onUndone?: (restoredCategoryId: string | null) => void
}

export function ProvenanceBlock({ transactionId, categories, onUndone }: ProvenanceBlockProps) {
  const { t } = useTranslation()
  const locale = useDateLocale()
  const queryClient = useQueryClient()
  // Read directly so the dialog keeps working outside a workspace (tests).
  const canWrite = useContext(WorkspaceContext)?.canWrite ?? false
  const [expanded, setExpanded] = useState<boolean | null>(null)
  const [undoing, setUndoing] = useState(false)

  const { data } = useQuery({
    queryKey: ['categorization', 'provenance', transactionId],
    queryFn: () => categorizationApi.provenance(transactionId),
    // Saving the row elsewhere does not invalidate this key: refetch on
    // every open rather than show an origin the last edit replaced.
    staleTime: 0,
  })

  if (!data) return null
  const source = data.current_source
  if (!EXPLAINED.includes(source) && data.events.length === 0) return null

  // Newest first: the latest change still in effect is what undo reverts.
  const latest: ProvenanceEvent | undefined = data.events.find((e) => !e.undone_at)
  const open = expanded ?? !COLLAPSED.includes(source)
  const style = source in ORIGIN_STYLES ? ORIGIN_STYLES[source as keyof typeof ORIGIN_STYLES] : null
  const Icon = style?.Icon ?? (source === 'user' ? UserRound : CircleDot)
  const label = t(`categorization.provenance.by_${LABELLED.includes(source) ? source : 'other'}`)

  const meta = [
    latest?.confidence != null ? t('categorization.provenance.confidence', { value: latest.confidence }) : null,
    latest ? formatRelativeFromNow(latest.created_at, locale) : null,
  ].filter(Boolean)

  const categoryName = (id: string | null) =>
    (id && categories.find((c) => c.id === id)?.name) || t('categorization.provenance.noCategory')
  const original = [
    data.original_description,
    latest ? categoryName(latest.previous_category_id) : null,
  ].filter(Boolean)

  const backToReview = !!latest && BACK_TO_REVIEW.includes(latest.source)

  const undo = async () => {
    if (!latest || undoing) return
    setUndoing(true)
    try {
      const result = await categorizationApi.undo({ event_ids: [latest.id] })
      if (result.restored > 0) {
        toast.success(t(backToReview ? 'categorization.provenance.undoneReview' : 'categorization.provenance.undone'))
        onUndone?.(latest.previous_category_id)
      } else {
        toast.info(t('categorization.provenance.undoSkipped'))
      }
    } catch (err) {
      toast.error(extractApiError(err, t('categorization.provenance.undoError')))
    } finally {
      setUndoing(false)
      invalidateCategorizationQueries(queryClient)
    }
  }

  return (
    <section
      aria-label={t('categorization.provenance.label')}
      className="p-3 text-sm bg-muted/50 border border-border rounded-md space-y-1.5"
    >
      <button
        type="button"
        onClick={() => setExpanded(!open)}
        aria-expanded={open}
        title={t('categorization.provenance.toggle')}
        className="flex w-full items-center gap-2 text-left"
      >
        <Icon className={cn('h-4 w-4 shrink-0', style?.iconClass ?? 'text-muted-foreground')} aria-hidden />
        <span className="min-w-0 flex-1 truncate">
          <span className="font-medium">{label}</span>
          {meta.length > 0 && <span className="text-muted-foreground"> · {meta.join(' · ')}</span>}
        </span>
        <ChevronDown
          className={cn('h-4 w-4 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')}
          aria-hidden
        />
      </button>
      {open && (
        <div className="flex items-end justify-between gap-3 pl-6">
          <div className="min-w-0 space-y-0.5">
            {latest?.reason && <p className="text-muted-foreground">{latest.reason}</p>}
            {original.length > 0 && (
              <p className="text-xs text-muted-foreground break-words">
                <span className="font-medium">{t('categorization.provenance.original')}:</span>{' '}
                {original.join(' · ')}
              </p>
            )}
          </div>
          {canWrite && latest && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="gap-1.5 shrink-0"
              onClick={undo}
              disabled={undoing}
              title={t(backToReview ? 'categorization.provenance.undoHintReview' : 'categorization.provenance.undoHint')}
            >
              {undoing ? <Loader2 size={14} className="animate-spin" /> : <Undo2 size={14} />}
              {t('categorization.provenance.undo')}
            </Button>
          )}
        </div>
      )}
    </section>
  )
}
