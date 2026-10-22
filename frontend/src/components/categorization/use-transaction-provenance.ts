import { useQuery } from '@tanstack/react-query'

import { categorization as categorizationApi } from '@/lib/api'
import type { CategorySource, Provenance } from '@/types'

/** Origins worth explaining even when no event was recorded. */
export const EXPLAINED_SOURCES: CategorySource[] = ['ai', 'memory', 'rule', 'provider']

/** The row's provenance, shared by the "how it got here" block and by a host
 *  that lays itself out around it (same key, so both read one request). */
export function useTransactionProvenance(transactionId: string | null | undefined) {
  return useQuery({
    queryKey: ['categorization', 'provenance', transactionId],
    queryFn: () => categorizationApi.provenance(transactionId!),
    enabled: !!transactionId,
    // Saving the row elsewhere does not invalidate this key: refetch on
    // every open rather than show an origin the last edit replaced.
    staleTime: 0,
  })
}

/** Whether the block has anything to say: an origin worth explaining or a
 *  recorded change. Otherwise it renders nothing. */
export function provenanceHasContent(data: Provenance | undefined): boolean {
  if (!data) return false
  return EXPLAINED_SOURCES.includes(data.current_source) || data.events.length > 0
}
