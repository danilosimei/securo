/** After a person fixes a category, offer the two shortcuts that save the
 *  next fixes: file the other rows of the same store the same way, or turn
 *  the store into a rule. Both ride on the confirmation toast. */
import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { categorization as categorizationApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateCategorizationQueries } from '@/lib/invalidate-queries'
import type { RuleDialogInitialData } from '@/components/rule-dialog'
import type { CategorySource, Transaction } from '@/types'

/** Long enough to read the offer and reach for it. */
const DURATION = 8000

interface Options {
  /** Opens the page's rule dialog; without it, "Create rule" is not offered. */
  onCreateRule?: (initialData: RuleDialogInitialData) => void
}

interface NotifyOptions {
  /** Confirmation line; "Category updated" by default. */
  title?: string
}

/** A save that filed the row under a different, non-empty category. */
export function categoryChanged(
  before: Pick<Transaction, 'category_id'> | null | undefined,
  payload: { category_id?: string | null },
): boolean {
  if (!before || !('category_id' in payload)) return false
  return !!payload.category_id && payload.category_id !== before.category_id
}

/**
 * The page's copy of the row being edited, after the dialog undid its
 * category server-side: without it, the next save would compare against the
 * undone category and offer shortcuts for a change nobody made.
 */
export function withUndoneCategory<T extends Pick<Transaction, 'id' | 'category_id' | 'category_source'>>(
  tx: T | null,
  txId: string,
  categoryId: string | null,
  source: CategorySource | null,
): T | null {
  if (!tx || tx.id !== txId) return tx
  return { ...tx, category_id: categoryId, category_source: source ?? tx.category_source }
}

const normalize = (value: string | null | undefined) =>
  (value ?? '').trim().replace(/\s+/g, ' ').toLowerCase()

/**
 * The row to offer the shortcuts for after a bulk change, when the whole
 * selection is one store: the same description and the same payee (or no
 * payee on any). Null otherwise, since "apply to similar" would then speak
 * for one store only; erring towards not offering is the safe side.
 */
export function singleStoreReference<T extends Pick<Transaction, 'id' | 'description' | 'payee_id'>>(
  rows: T[],
): T | null {
  const [first] = rows
  if (!first) return null
  const description = normalize(first.description)
  if (!description) return null
  const payee = first.payee_id ?? null
  const same = rows.every((r) => normalize(r.description) === description && (r.payee_id ?? null) === payee)
  return same ? first : null
}

export function useCategoryCorrectionToast({ onCreateRule }: Options = {}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const undo = useCallback(
    async (eventIds: string[]) => {
      try {
        const result = await categorizationApi.undo({ event_ids: eventIds })
        if (result.restored > 0) {
          toast.success(t('categorization.correction.undone', { count: result.restored }))
        } else if (result.skipped > 0) {
          toast.info(t('categorization.applied.skipped', { count: result.skipped }))
        }
      } catch (err) {
        toast.error(extractApiError(err, t('categorization.correction.undoError')))
      } finally {
        invalidateCategorizationQueries(queryClient)
      }
    },
    [queryClient, t],
  )

  const applySimilar = useCallback(
    async (transactionId: string) => {
      try {
        const result = await categorizationApi.applySimilar(transactionId)
        invalidateCategorizationQueries(queryClient)
        if (result.applied > 0) {
          toast.success(t('categorization.correction.applied', { count: result.applied }), {
            action: {
              label: t('categorization.correction.undo'),
              onClick: () => void undo(result.event_ids),
            },
            duration: DURATION,
          })
        }
      } catch (err) {
        toast.error(extractApiError(err, t('categorization.correction.applyError')))
      }
    },
    [queryClient, t, undo],
  )

  const notifyCorrection = useCallback(
    async (tx: Pick<Transaction, 'id'>, { title }: NotifyOptions = {}) => {
      const message = title ?? t('categorization.correction.updated')
      // Confirm at once; the offers are added to the same toast when known.
      // No canWrite check: this only runs after the person's own save went
      // through, which a viewer cannot make.
      // Already long, so it cannot expire and pop back while the offers load.
      const id = toast.success(message, { duration: DURATION })

      const [similar, draft] = await Promise.allSettled([
        categorizationApi.similar(tx.id),
        categorizationApi.ruleDraft(tx.id),
      ])
      const count = similar.status === 'fulfilled' ? similar.value.count : 0
      // A 422 means the store has no safe pattern: no rule to offer.
      const rule = draft.status === 'fulfilled' && draft.value.conditions.length > 0 ? draft.value : null

      const apply =
        count > 0
          ? {
              label: t('categorization.correction.applySimilar', { count }),
              onClick: () => void applySimilar(tx.id),
            }
          : undefined
      const createRule =
        rule && onCreateRule
          ? {
              label: t('categorization.correction.createRule'),
              onClick: () =>
                onCreateRule({
                  name: rule.name,
                  conditions: rule.conditions,
                  actions: rule.actions,
                  applyToExisting: true,
                }),
            }
          : undefined

      if (!apply && !createRule) return
      toast.success(message, {
        id,
        action: apply ?? createRule,
        cancel: apply ? createRule : undefined,
        duration: DURATION,
      })
    },
    [applySimilar, onCreateRule, t],
  )

  return { notifyCorrection }
}
