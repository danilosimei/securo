import { describe, expect, it } from 'vitest'

import { decidedPercent, scoreboardBar } from '@/lib/categorization-scoreboard'

const board = (total: number, decided: number, toReview: number) => ({
  total,
  decided,
  to_review: toReview,
  waiting: total - decided - toReview,
})

describe('decidedPercent', () => {
  it('porcentagem inteira das decididas', () => {
    expect(decidedPercent(board(120, 90, 30))).toBe(75)
    expect(decidedPercent(board(3, 1, 1))).toBe(33)
  })

  it('nunca diz 100% enquanto falta alguma', () => {
    expect(decidedPercent(board(3693, 3678, 12))).toBe(99)
    expect(decidedPercent(board(1000, 999, 1))).toBe(99)
    expect(decidedPercent(board(1000, 999, 0))).toBe(99)
  })

  it('100% só quando todas estão decididas; base vazia é 0', () => {
    expect(decidedPercent(board(4, 4, 0))).toBe(100)
    expect(decidedPercent(board(0, 0, 0))).toBe(0)
  })
})

describe('scoreboardBar', () => {
  it('fatias proporcionais às contagens', () => {
    expect(scoreboardBar(board(4, 3, 1))).toEqual({ decided: 75, toReview: 25 })
    expect(scoreboardBar(board(0, 0, 0))).toEqual({ decided: 0, toReview: 0 })
  })
})
