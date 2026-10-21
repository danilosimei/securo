/** A link from Reports > Explore filters the list by store (`merchant_key`). */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { waitFor } from '@testing-library/react'

import TransactionsPage from '@/pages/transactions'
import { TooltipProvider } from '@/components/ui/tooltip'
import { renderWithProviders } from '@/test/utils'

const api = vi.hoisted(() => {
  const fn = () => vi.fn().mockResolvedValue([])
  return {
    transactions: { list: vi.fn(), calendar: vi.fn().mockResolvedValue({ days: [] }), update: vi.fn(), get: vi.fn() },
    categories: { list: fn(), listIncludingHidden: fn() },
    categoryGroups: { list: fn() },
    accounts: { list: fn() },
    payees: { list: fn() },
    recurring: { list: fn() },
    admin: { accountingMode: vi.fn().mockResolvedValue({ mode: 'cash' }) },
    groups: { list: fn(), get: vi.fn() },
    reconciliation: { suggestions: fn() },
    rules: { create: vi.fn() },
  }
})

vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))
vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => false }))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { id: 'user-1', preferences: { currency_display: 'USD' } } }),
}))
vi.mock('@/contexts/workspace-context', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/workspace-context')>()),
  useWorkspace: () => ({ canWrite: true, canManage: true, hasModule: () => true }),
}))
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null, activeWalletIds: null }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  api.transactions.list.mockResolvedValue({ items: [], total: 0 })
})

describe('transactions page: merchant_key', () => {
  it('passes the store key from the URL to the API', async () => {
    renderWithProviders(
      <TooltipProvider>
        <TransactionsPage />
      </TooltipProvider>,
      { route: '/transactions?from=2025-10-01&to=2026-09-30&category_id=c1&merchant_key=stellantis' },
    )

    await waitFor(() => expect(api.transactions.list).toHaveBeenCalled())
    expect(api.transactions.list).toHaveBeenCalledWith(
      expect.objectContaining({
        merchant_key: 'stellantis',
        category_ids: ['c1'],
        from: '2025-10-01',
        to: '2026-09-30',
      }),
    )
  })

  it('sends no store key when the URL has none', async () => {
    renderWithProviders(
      <TooltipProvider>
        <TransactionsPage />
      </TooltipProvider>,
      { route: '/transactions' },
    )

    await waitFor(() => expect(api.transactions.list).toHaveBeenCalled())
    expect(api.transactions.list).toHaveBeenCalledWith(expect.objectContaining({ merchant_key: undefined }))
  })
})
