import { describe, expect, it } from 'vitest'

import { scoreboardParts } from '@/lib/categorization-scoreboard'

describe('scoreboardParts', () => {
  it('reparte pelo maior resto, empate na ordem decididas, revisar, aguardando', () => {
    expect(scoreboardParts({ total: 3, decided: 1, to_review: 1, waiting: 1 })).toEqual({
      decided: 34,
      toReview: 33,
      waiting: 33,
    })
  })

  it('base vazia: tudo zero', () => {
    expect(scoreboardParts({ total: 0, decided: 0, to_review: 0, waiting: 0 })).toEqual({
      decided: 0,
      toReview: 0,
      waiting: 0,
    })
  })

  it('divisão exata fica como é', () => {
    expect(scoreboardParts({ total: 4, decided: 3, to_review: 1, waiting: 0 })).toEqual({
      decided: 75,
      toReview: 25,
      waiting: 0,
    })
  })

  it('o resto vai para quem tem a maior fração, não para o primeiro', () => {
    // 1 / 3 / 3 of 7 → 14.29 / 42.86 / 42.86: the two points skip decided.
    expect(scoreboardParts({ total: 7, decided: 1, to_review: 3, waiting: 3 })).toEqual({
      decided: 14,
      toReview: 43,
      waiting: 43,
    })
    // 16.67 / 16.67 / 66.67 → floors 16/16/66 (98): three equal remainders,
    // so the two points go in order, decided first then toReview.
    expect(scoreboardParts({ total: 6, decided: 1, to_review: 1, waiting: 4 })).toEqual({
      decided: 17,
      toReview: 17,
      waiting: 66,
    })
    // 12.5 / 87.5: one point left, the tie goes to decided.
    expect(scoreboardParts({ total: 8, decided: 1, to_review: 7, waiting: 0 })).toEqual({
      decided: 13,
      toReview: 87,
      waiting: 0,
    })
  })

  it('sempre soma 100', () => {
    for (let total = 1; total <= 40; total++) {
      for (let decided = 0; decided <= total; decided++) {
        const toReview = Math.floor((total - decided) / 2)
        const parts = scoreboardParts({ total, decided, to_review: toReview, waiting: total - decided - toReview })
        expect(parts.decided + parts.toReview + parts.waiting).toBe(100)
      }
    }
  })
})
