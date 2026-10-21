/** The Categorization section's scoreboard as whole percentages. */
import type { Scoreboard } from '@/types'

export interface ScoreboardParts {
  decided: number
  toReview: number
  waiting: number
}

/** Whole percentages that add up to 100 (largest remainder; a tie goes to
 *  decided, then to review, then waiting). An empty base is all zeros. */
export function scoreboardParts(s: Scoreboard): ScoreboardParts {
  if (s.total <= 0) return { decided: 0, toReview: 0, waiting: 0 }
  // Integer arithmetic, so equal remainders compare equal.
  const scaled = [s.decided, s.to_review, s.waiting].map((n) => n * 100)
  const whole = scaled.map((n) => Math.floor(n / s.total))
  const remainder = scaled.map((n) => n % s.total)
  let left = 100 - whole.reduce((sum, n) => sum + n, 0)
  // Stable sort: equal remainders keep the order decided, toReview, waiting.
  const byRemainder = [0, 1, 2].sort((a, b) => remainder[b] - remainder[a])
  for (const i of byRemainder) {
    if (left <= 0) break
    whole[i] += 1
    left -= 1
  }
  return { decided: whole[0], toReview: whole[1], waiting: whole[2] }
}
