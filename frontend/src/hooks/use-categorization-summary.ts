import { useQuery } from '@tanstack/react-query'
import { categorization } from '@/lib/api'
import type { CategorizationSummary } from '@/types'

export const CATEGORIZATION_SUMMARY_KEY = ['categorization', 'summary'] as const
const BUSY_STATUSES = ['queued', 'running', 'processing_batch']
const POLL_MS = 5000

/** A run is in flight: the worker has it queued, running, or waiting on a batch. */
export function isCategorizationBusy(summary: CategorizationSummary | undefined) {
  return !!summary?.last_run && BUSY_STATUSES.includes(summary.last_run.status)
}

/**
 * The one reader of the summary, shared by the page and the sidebar so they
 * poll together: every 5 s while a run is busy, never otherwise.
 */
export function useCategorizationSummary() {
  return useQuery({
    queryKey: [...CATEGORIZATION_SUMMARY_KEY],
    queryFn: categorization.summary,
    staleTime: 60_000,
    refetchInterval: (query) => (isCategorizationBusy(query.state.data) ? POLL_MS : false),
  })
}

/** Groups waiting for a person to review; 0 while loading. */
export function useCategorizationPending(): number {
  return useCategorizationSummary().data?.pending_groups ?? 0
}
