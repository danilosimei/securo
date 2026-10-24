/** Undo for the Categorization section's toasts: the events a person's
 *  answer wrote go back, in blocks the endpoint takes, and every
 *  categorization view refreshes either way. */
import type { QueryClient } from '@tanstack/react-query'
import type { TFunction } from 'i18next'
import { toast } from 'sonner'

import { categorization as categorizationApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateCategorizationQueries } from '@/lib/invalidate-queries'

/** The undo endpoint takes at most this many event ids per call. */
export const UNDO_CHUNK = 5000

/** In order, one block at a time; a failed block stops the rest. */
export async function undoEvents(eventIds: string[]): Promise<void> {
  for (let i = 0; i < eventIds.length; i += UNDO_CHUNK) {
    await categorizationApi.undo({ event_ids: eventIds.slice(i, i + UNDO_CHUNK) })
  }
}

/** `undoEvents` with the "Undone" / error toast and the refresh. */
export async function undoWithToast(
  eventIds: string[],
  { t, queryClient }: { t: TFunction; queryClient: QueryClient },
): Promise<void> {
  try {
    await undoEvents(eventIds)
    toast.success(t('categorization.review.undone'))
  } catch (err) {
    toast.error(extractApiError(err, t('common.error')))
  } finally {
    invalidateCategorizationQueries(queryClient)
  }
}
