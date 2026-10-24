import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { useLocation } from 'react-router-dom'

import AssetsPage from '@/pages/assets'
import { renderWithProviders, t } from '@/test/utils'
import type { Asset, AssetGroup } from '@/types'

const api = vi.hoisted(() => ({
  assets: { list: vi.fn(), portfolioTrend: vi.fn() },
  assetGroups: { list: vi.fn() },
  currencies: { list: vi.fn() },
}))

vi.mock('@/lib/api', () => api)

vi.mock('@/contexts/workspace-context', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/workspace-context')>()),
  useWorkspace: () => ({ canWrite: true, canManage: true }),
}))

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null, activeWalletIds: null }),
}))

function asset(overrides: Partial<Asset>): Asset {
  return {
    id: 'a', user_id: 'u', name: 'Asset', type: 'stock', currency: 'USD', units: null,
    valuation_method: 'manual', purchase_date: null, purchase_price: null, sell_date: null, sell_price: null,
    growth_type: null, growth_rate: null, growth_frequency: null, growth_start_date: null, is_archived: false,
    position: 0, current_value: 0, current_value_primary: null, gain_loss: null, gain_loss_primary: null,
    value_count: 1, source: 'manual', connection_id: null, isin: null, maturity_date: null, group_id: null,
    ticker: null, ticker_exchange: null, last_price: null, last_price_at: null, logo_url: null,
    average_price: null, total_invested: null, realized_gain: null, transaction_count: 0,
    ...overrides,
  }
}

function wallet(overrides: Partial<AssetGroup>): AssetGroup {
  return {
    id: 'w', user_id: 'u', name: 'Wallet', icon: 'wallet', color: '#0EA5E9', position: 0, source: 'manual',
    connection_id: null, institution_name: null, asset_count: 1, current_value: 0, current_value_primary: 0,
    ...overrides,
  }
}

const ASSETS = [
  asset({ id: 'a1', name: 'Apple Inc', group_id: 'w1', current_value: 1000 }),
  asset({ id: 'a2', name: 'Fundo XP', type: 'fund', group_id: 'w2', current_value: 500 }),
  asset({ id: 'a3', name: 'Apartamento', type: 'real_estate', current_value: 300000 }),
  asset({ id: 'a4', name: 'Old car', type: 'vehicle', current_value: 0, sell_date: '2026-01-10', sell_price: 9000 }),
]

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.search}</output>
}

function renderPage(route = '/') {
  return renderWithProviders(
    <>
      <AssetsPage />
      <LocationProbe />
    </>,
    { route },
  )
}

const chip = (name: string) =>
  within(screen.getByRole('group', { name: t('assets.sumFilter.label') })).getByRole('button', { name })

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  api.assets.list.mockResolvedValue(ASSETS)
  api.assetGroups.list.mockResolvedValue([
    wallet({ id: 'w1', name: 'Avenue', position: 0 }),
    wallet({ id: 'w2', name: 'XP', position: 1, color: '#F59E0B' }),
  ])
  api.currencies.list.mockResolvedValue([])
  api.assets.portfolioTrend.mockResolvedValue({
    assets: [
      { id: 'a1', name: 'Apple Inc', type: 'stock', group_id: 'w1' },
      { id: 'a2', name: 'Fundo XP', type: 'fund', group_id: 'w2' },
      { id: 'a3', name: 'Apartamento', type: 'real_estate', group_id: null },
    ],
    trend: [
      { date: '2026-09-01', a1: 900, a2: 500, a3: 300000, _total: 301400 },
      { date: '2026-10-01', a1: 1000, a2: 500, a3: 300000, _total: 301500 },
    ],
    total: 301500,
  })
})

describe('Assets "Somar" filter', () => {
  it('has one chip per wallet and per asset outside a wallet, all on, and no sold asset', async () => {
    renderPage()
    await waitFor(() => expect(chip('Avenue')).toBeInTheDocument())
    for (const name of ['Avenue', 'XP', 'Apartamento']) expect(chip(name)).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getAllByTestId('holdings-filter-chip')).toHaveLength(3)
    expect(screen.queryByTestId('holdings-filter-summary')).not.toBeInTheDocument()
    expect(screen.getByText('$301,500.00')).toBeInTheDocument()
  })

  it('turning a chip off takes it out of the total, the chart and the list, and "Show all" brings it back', async () => {
    const { user } = renderPage()
    await waitFor(() => expect(chip('Apartamento')).toBeInTheDocument())
    expect(screen.getByText(t('assets.ungrouped'))).toBeInTheDocument()

    await user.click(chip('Apartamento'))

    expect(chip('Apartamento')).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByTestId('holdings-filter-summary')).toHaveTextContent(
      t('assets.sumFilter.summary', { shown: 2, total: 3, value: '$1,500.00' }),
    )
    // The chart total sums only what is on; the asset leaves the list.
    expect(screen.queryByText('$301,500.00')).not.toBeInTheDocument()
    expect(screen.getAllByText('$1,500.00').length).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText(t('assets.ungrouped'))).not.toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('?hide=a3')
    expect(localStorage.getItem('securo.assets.hide')).toBe('a3')

    await user.click(within(screen.getByTestId('holdings-filter-summary')).getByRole('button', { name: t('assets.sumFilter.showAll') }))

    expect(chip('Apartamento')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByTestId('holdings-filter-summary')).not.toBeInTheDocument()
    expect(screen.getByText('$301,500.00')).toBeInTheDocument()
    expect(screen.getByTestId('location')).toBeEmptyDOMElement()
    expect(localStorage.getItem('securo.assets.hide')).toBeNull()
  })

  it('a wallet off hides its holdings', async () => {
    const { user } = renderPage()
    await waitFor(() => expect(screen.getByText('Fundo XP')).toBeInTheDocument())
    await user.click(chip('XP'))
    expect(screen.queryByText('Fundo XP')).not.toBeInTheDocument()
    expect(screen.getByText('Apple Inc')).toBeInTheDocument()
    expect(screen.getByTestId('holdings-filter-summary')).toHaveTextContent(
      t('assets.sumFilter.summary', { shown: 2, total: 3, value: '$301,000.00' }),
    )
  })

  it('"Investments only" turns off property, vehicles and valuables', async () => {
    const { user } = renderPage()
    await waitFor(() => expect(chip('Apartamento')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: t('assets.sumFilter.investmentsOnly') }))
    expect(chip('Apartamento')).toHaveAttribute('aria-pressed', 'false')
    expect(chip('Avenue')).toHaveAttribute('aria-pressed', 'true')
    expect(chip('XP')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('holdings-filter-summary')).toHaveTextContent('$1,500.00')

    await user.click(screen.getByRole('button', { name: t('assets.sumFilter.all') }))
    expect(chip('Apartamento')).toHaveAttribute('aria-pressed', 'true')
  })

  it('reads what is off from the URL, and otherwise from the last visit', async () => {
    const first = renderPage('/?hide=w2,gone')
    await waitFor(() => expect(chip('XP')).toHaveAttribute('aria-pressed', 'false'))
    expect(screen.queryByText('Fundo XP')).not.toBeInTheDocument()
    // An id that is no longer a chip changes nothing.
    expect(screen.getByTestId('holdings-filter-summary')).toHaveTextContent(
      t('assets.sumFilter.summary', { shown: 2, total: 3, value: '$301,000.00' }),
    )
    first.unmount()

    renderPage('/')
    await waitFor(() => expect(chip('XP')).toHaveAttribute('aria-pressed', 'false'))
    expect(chip('Avenue')).toHaveAttribute('aria-pressed', 'true')
  })

  it('only applies to the Positions tab', async () => {
    renderPage('/?tab=transactions&hide=w1')
    await waitFor(() => expect(api.assets.list).toHaveBeenCalled())
    expect(screen.queryByTestId('holdings-filter')).not.toBeInTheDocument()
  })
})
