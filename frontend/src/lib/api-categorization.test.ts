import { afterEach, describe, expect, it, vi } from 'vitest'

import api, { categorization } from '@/lib/api'

describe('categorization.dismiss', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('posts through the shared instance', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: { dismissed: 1 } })

    await expect(categorization.dismiss('IFOOD')).resolves.toEqual({ dismissed: 1 })
    expect(post).toHaveBeenCalledWith('/categorization/review/dismiss', { merchant_key: 'IFOOD' }, undefined)
  })

  it('keepalive goes through fetch, so it survives the tab closing', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: { dismissed: 1 } })

    await categorization.dismiss('IFOOD', { keepalive: true })
    expect(post).toHaveBeenCalledWith(
      '/categorization/review/dismiss',
      { merchant_key: 'IFOOD' },
      { adapter: 'fetch', fetchOptions: { keepalive: true } },
    )
  })
})
