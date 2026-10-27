import { describe, expect, it } from 'vitest'

import { actionHeaders, newActionId } from '@/lib/action-id'

describe('newActionId', () => {
  it('is a fresh version 4 uuid each time', () => {
    const a = newActionId()
    expect(a).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    expect(newActionId()).not.toBe(a)
  })
})

describe('actionHeaders', () => {
  it('names the action only when there is one', () => {
    expect(actionHeaders('x')).toEqual({ headers: { 'X-Action-Id': 'x' } })
    expect(actionHeaders()).toEqual({})
  })
})
