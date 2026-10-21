/** The Categorization section's scoreboard: decided as a share, the rest
 *  as counts (a share would read "0% to review" while rows remain). */
import type { Scoreboard } from '@/types'

/** Whole percent decided, rounded down: never 100% while any row is left
 *  (to review or waiting). An empty base is 0. */
export function decidedPercent(s: Scoreboard): number {
  if (s.total <= 0) return 0
  return Math.floor((s.decided * 100) / s.total)
}

/** The bar's slices, in percent of the base (unrounded). */
export function scoreboardBar(s: Scoreboard): { decided: number; toReview: number } {
  if (s.total <= 0) return { decided: 0, toReview: 0 }
  return { decided: (s.decided * 100) / s.total, toReview: (s.to_review * 100) / s.total }
}
