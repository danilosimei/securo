import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import ReportsPage from '@/pages/reports'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({
  reports: {
    netWorth: vi.fn(),
    incomeExpenses: vi.fn(),
    cashFlow: vi.fn(),
    breakdown: vi.fn(),
  },
  timezones: { list: vi.fn().mockResolvedValue({ timezones: [], default: null }) },
}))

vi.mock('@/lib/api', () => ({ reports: api.reports, timezones: api.timezones }))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null, activeWalletIds: null }),
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  api.reports.netWorth.mockResolvedValue({
    summary: { primary_value: 1, change_amount: 0, change_percent: 0, breakdowns: [] },
    trend: [],
    meta: { type: 'net_worth', series_keys: [], currency: 'USD', interval: 'monthly' },
    composition: [],
    category_trend: [],
  })
  api.reports.breakdown.mockResolvedValue({
    period: { from: '2026-03-01', to: '2026-08-31', partial: false, previous_from: '2025-09-01', previous_to: '2026-02-28' },
    currency: 'BRL',
    total: '10.00',
    previous_total: '5.00',
    rows: [{ id: 'g1', label: 'Compras', kind: 'group', total: '10.00', share: 1, previous_total: '5.00', change: 1, count: 1, sparkline: [] }],
    unconverted_count: 0,
  })
})

describe('Reports page: Explore tab', () => {
  it('opens on Explore with the period from the URL', async () => {
    renderWithProviders(<ReportsPage />, { route: '/reports?tab=explore&period=last_6m' })

    expect(await screen.findByText('Compras')).toBeInTheDocument()
    expect(api.reports.breakdown).toHaveBeenCalledWith({ level: 'group', period: 'last_6m' })
    expect(screen.getByRole('button', { name: t('reports.explore.period.last_6m') })).toHaveAttribute('aria-pressed', 'true')
    expect(api.reports.netWorth).not.toHaveBeenCalled()
  })

  it('switches to another tab and back, and Explore is listed first', async () => {
    const { user } = renderWithProviders(<ReportsPage />, { route: '/reports?tab=explore' })
    await screen.findByText('Compras')

    const tabs = screen.getAllByRole('button').filter((b) => b.className.includes('px-4 py-2.5'))
    expect(tabs[0]).toHaveTextContent(t('reports.explore.tab'))

    await user.click(screen.getByRole('button', { name: t('reports.netWorth') }))
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalled())
    expect(screen.queryByText('Compras')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('reports.explore.tab') }))
    expect(await screen.findByText('Compras')).toBeInTheDocument()
  })
})
