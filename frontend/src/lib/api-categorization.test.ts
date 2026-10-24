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

describe('categorization.dismiss through the real adapter', () => {
  const baseURL = api.defaults.baseURL

  afterEach(() => {
    api.defaults.baseURL = baseURL
    vi.unstubAllGlobals()
  })

  it('sends a keepalive fetch request that still carries the auth and workspace headers', async () => {
    const store = new Map([
      ['token', 'tok-123'],
      ['workspace_id', 'ws-9'],
    ])
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: () => {},
      removeItem: () => {},
    })
    const sent: Request[] = []
    const fetchStub = vi.fn(async (input: Request | string, init?: RequestInit) => {
      sent.push(input instanceof Request ? input : new Request(input, init))
      return new Response(JSON.stringify({ dismissed: 1 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchStub)
    // Node's Request needs an absolute URL; the browser resolves '/api'.
    api.defaults.baseURL = 'http://localhost/api'

    await expect(categorization.dismiss('IFOOD', { keepalive: true })).resolves.toEqual({ dismissed: 1 })

    expect(fetchStub).toHaveBeenCalledTimes(1)
    const [request] = sent
    expect(request.url).toBe('http://localhost/api/categorization/review/dismiss')
    expect(request.keepalive).toBe(true)
    expect(request.headers.get('Authorization')).toBe('Bearer tok-123')
    expect(request.headers.get('X-Workspace-Id')).toBe('ws-9')
    expect(await request.clone().json()).toEqual({ merchant_key: 'IFOOD' })
  })
})
