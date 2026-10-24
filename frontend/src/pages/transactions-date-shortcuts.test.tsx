/** Transactions' date shortcuts are the Reports periods: picking one sets the
 *  same from/to, keeps the other filters and writes the range to the URL. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import TransactionsPage from '@/pages/transactions'
import { TooltipProvider } from '@/components/ui/tooltip'
import { renderWithProviders, t } from '@/test/utils'

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

afterEach(() => vi.useRealTimers())

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 9, 21, 12))
  vi.clearAllMocks()
  api.transactions.list.mockResolvedValue({ items: [], total: 0 })
})

async function pickShortcut(user: { click: (el: Element) => Promise<void>; keyboard: (keys: string) => Promise<void> }, period: string) {
  await user.click(screen.getAllByRole('button', { name: new RegExp(t('transactions.filtersBar.filters')) })[0])
  const triggers = await screen.findAllByRole('menuitem', { name: new RegExp(t('transactions.filtersBar.date')) })
  await user.click(triggers.find((el) => el.getAttribute('aria-haspopup') === 'menu')!)
  const item = await screen.findByRole('menuitem', { name: t(`reports.explore.period.${period}`) })
  item.focus()
  await user.keyboard('{Enter}')
}

describe('transactions page: date shortcuts', () => {
  it('Last 3 months asks for the three closed months, keeps the other filters and goes to the URL', async () => {
    const { user } = renderWithProviders(
      <TooltipProvider>
        <TransactionsPage />
      </TooltipProvider>,
      { route: '/transactions?category_id=c1&from=2026-10-01&to=2026-10-31' },
    )
    await waitFor(() => expect(api.transactions.list).toHaveBeenCalled())

    await pickShortcut(user, 'last_3m')

    await waitFor(() =>
      expect(api.transactions.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ category_ids: ['c1'], from: '2026-07-01', to: '2026-09-30' }),
      ),
    )
    const params = new URLSearchParams(window.location.search)
    expect([params.get('from'), params.get('to'), params.get('category_id')]).toEqual(['2026-07-01', '2026-09-30', 'c1'])
  })

  it('Last month shows that month in the header month control', async () => {
    const { user } = renderWithProviders(
      <TooltipProvider>
        <TransactionsPage />
      </TooltipProvider>,
      { route: '/transactions?from=2026-10-01&to=2026-10-31' },
    )
    await waitFor(() => expect(api.transactions.list).toHaveBeenCalled())

    await pickShortcut(user, 'last_month')

    await waitFor(() =>
      expect(api.transactions.list).toHaveBeenLastCalledWith(expect.objectContaining({ from: '2026-09-01', to: '2026-09-30' })),
    )
    expect(screen.getByTestId('period-menu')).toHaveAttribute('title', 'September 2026')
  })
})
