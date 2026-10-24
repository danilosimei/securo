import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { useLocation } from 'react-router-dom'

import { ExploreTab } from '@/components/reports/explore/explore-tab'
import { concentration, heatStep, trendSeries } from '@/components/reports/explore/explore-model'
import { renderWithProviders, t } from '@/test/utils'
import type { BreakdownParams, BreakdownResponse, BreakdownRow } from '@/lib/api'

const api = vi.hoisted(() => ({
  reports: { breakdown: vi.fn() },
  transactions: { list: vi.fn() },
  categories: { list: vi.fn().mockResolvedValue([]) },
  categoryGroups: { list: vi.fn().mockResolvedValue([]) },
  accounts: { list: vi.fn().mockResolvedValue([]) },
}))

vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))
vi.mock('@/components/transaction-dialog', () => ({ TransactionDialog: () => null }))

const spark = (last: number) => Array.from({ length: 12 }, (_, i) => String(i === 11 ? last : 10 * (i + 1)))
const row = (over: Partial<BreakdownRow>): BreakdownRow => ({
  id: 'x', label: 'X', kind: 'group', total: '100.00', share: 0.1, previous_total: '50.00', change: 1, count: 3, sparkline: spark(100), ...over,
})
const PERIOD = { from: '2025-10-01', to: '2026-09-30', partial: false, previous_from: '2024-10-01', previous_to: '2025-09-30' }
const resp = (rows: BreakdownRow[], over: Partial<BreakdownResponse> = {}): BreakdownResponse => ({
  period: PERIOD, currency: 'BRL', total: '1000.00', previous_total: '800.00', rows, unconverted_count: 0, ...over,
})

const GROUPS = [
  row({ id: 'g1', label: 'Casa', total: '600.00', previous_total: '400.00', sparkline: spark(600) }),
  row({ id: 'g2', label: 'Carro', total: '300.00', previous_total: '350.00', sparkline: spark(300) }),
  row({ id: 'g3', label: 'Lazer', total: '100.00', previous_total: '50.00', sparkline: spark(0) }),
]
const STORES = [
  row({ id: 'AZZA', kind: 'merchant', label: 'Azza', total: '500.00', category_id: 'c-tax', category_label: 'Impostos & Taxas' }),
  row({ id: 'IFOOD', kind: 'merchant', label: 'iFood', total: '300.00', category_id: 'c-food', category_label: 'Restaurantes' }),
]
const ACCOUNTS = [
  row({ id: 'a-nu', kind: 'account', label: 'Nubank', total: '700.00' }),
  row({ id: 'a-bb', kind: 'account', label: 'Banco do Brasil', total: '300.00' }),
]

function Probe() {
  const location = useLocation()
  return <output data-testid="url">{location.search}</output>
}
const ui = (
  <>
    <ExploreTab />
    <Probe />
  </>
)
const url = () => screen.getByTestId('url').textContent ?? ''
const inTable = async (text: string) => within(await screen.findByTestId('explore-table')).findByText(text)

beforeEach(() => {
  vi.clearAllMocks()
  api.reports.breakdown.mockImplementation(async (p: BreakdownParams) =>
    resp(p.level === 'account' ? ACCOUNTS : p.level === 'merchant' ? STORES : GROUPS),
  )
  api.transactions.list.mockResolvedValue({
    items: [], total: 0, page: 1, limit: 50, pages: 1,
    summary: { income: 0, expense: 0, net: 0, excluded: 0, currency: 'BRL' },
  })
})

describe('Explore: show as', () => {
  it('opens as bars, biggest first, with value and share written out', async () => {
    renderWithProviders(ui, { route: '/reports?tab=explore' })
    const bars = await screen.findAllByTestId('explore-bar')
    expect(bars.map((b) => b.getAttribute('aria-label'))).toEqual([
      'Casa: R$600.00 (60%)',
      'Carro: R$300.00 (30%)',
      'Lazer: R$100.00 (10%)',
    ])
    expect(screen.getByTestId('explore-chart-bars')).toHaveAttribute('aria-checked', 'true')
  })

  it('switches to blocks and to lines, keeping the choice in the URL and the table below', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore' })
    await screen.findAllByTestId('explore-bar')

    await user.click(screen.getByTestId('explore-chart-treemap'))
    expect(await screen.findByTestId('explore-treemap')).toBeInTheDocument()
    expect(url()).toContain('chart=treemap')

    await user.click(screen.getByTestId('explore-chart-trend'))
    const trend = await screen.findByTestId('explore-trend')
    expect(within(trend).getByRole('button', { name: 'Casa' })).toBeInTheDocument()
    expect(url()).toContain('chart=trend')
    expect(screen.getByTestId('explore-table')).toBeInTheDocument()
  })

  it('a bar goes down a level, like its row', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore' })
    await user.click((await screen.findAllByTestId('explore-bar'))[0])
    await waitFor(() => expect(url()).toContain('group=g1'))
  })
})

describe('Explore: view by', () => {
  it('Store lists every store of the period with its category and opens only that store', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore' })
    await inTable('Casa')
    await user.click(screen.getByTestId('explore-view-merchant'))

    await waitFor(() =>
      expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'merchant', category_id: 'all', period: 'last_12m' }),
    )
    const azza = (await inTable('Azza')).closest('tr') as HTMLElement
    expect(azza).toHaveTextContent('Impostos & Taxas')

    await user.click(azza)
    expect(await screen.findByTestId('explore-store-title')).toHaveTextContent('Azza · Impostos & Taxas')
    expect(screen.queryByTestId('explore-table')).not.toBeInTheDocument()
    // Every category of the store: no category filter on its transactions.
    await waitFor(() =>
      expect(api.transactions.list).toHaveBeenCalledWith(expect.objectContaining({ merchant_key: 'AZZA' })),
    )
    expect(api.transactions.list.mock.calls[0][0]).not.toHaveProperty('category_id')

    await user.click(screen.getByRole('button', { name: t('reports.explore.backToStores') }))
    expect(await inTable('iFood')).toBeInTheDocument()
  })

  it('Account splits by account and a click opens the categories of that account', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore&view=account' })
    await user.click(await inTable('Nubank'))

    await waitFor(() => expect(url()).toContain('account=a-nu'))
    expect(url()).not.toContain('view=')
    await waitFor(() =>
      expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'group', account_id: 'a-nu', period: 'last_12m' }),
    )
    const chip = screen.getByTestId('explore-account-filter')
    expect(chip).toHaveTextContent(t('reports.explore.accountFilter', { name: 'Nubank' }))

    // Going down keeps the account.
    await user.click(await inTable('Casa'))
    await waitFor(() =>
      expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'category', group_id: 'g1', account_id: 'a-nu', period: 'last_12m' }),
    )
    await user.click(within(screen.getByTestId('explore-account-filter')).getByRole('button', { name: t('reports.explore.clearAccount') }))
    await waitFor(() => expect(url()).not.toContain('account='))
  })

  it('Month draws a table of colors and a cell opens that group in that month', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore&view=month' })
    const matrix = await screen.findByTestId('month-matrix')
    expect(matrix).toHaveTextContent(t('reports.explore.month.legend'))
    const casa = within(matrix).getAllByTestId('month-row')[0]
    const cells = within(casa).getAllByTestId('month-cell')
    expect(cells).toHaveLength(12)
    // September, the biggest cell of the table, is the darkest.
    expect(cells[11]).toHaveAttribute('data-step', '5')
    expect(cells[11]).toHaveAccessibleName('Casa, September 2026: R$600.00')
    // Lazer spent nothing in September: an empty cell that opens nothing.
    const lazer = within(matrix).getAllByTestId('month-row')[2]
    expect(within(lazer).getAllByTestId('month-cell')[11]).toBeDisabled()

    await user.click(cells[11])
    await waitFor(() => expect(url()).toContain('group=g1'))
    expect(url()).toContain('period=custom&from=2026-09-01&to=2026-09-30')
    expect(url()).not.toContain('view=')
  })

  it('Month by category asks for every category', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore&view=month' })
    await screen.findByTestId('month-matrix')
    await user.click(screen.getByTestId('month-mode-categories'))
    await waitFor(() =>
      expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'category', group_id: 'all', period: 'last_12m' }),
    )
  })

  it('Compare puts A and B side by side with the difference in words', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore&view=compare' })
    const view = await screen.findByTestId('compare-view')
    await waitFor(() =>
      expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'group', compare: 'previous', period: 'last_12m' }),
    )
    const casa = (await within(view).findByText('Casa')).closest('tr') as HTMLElement
    expect(casa).toHaveTextContent(t('reports.explore.compare.more', { value: 'R$200' }))
    const carro = within(view).getByText('Carro').closest('tr') as HTMLElement
    expect(carro).toHaveTextContent(t('reports.explore.compare.less', { value: 'R$50' }))
    expect(screen.getByTestId('report-headline')).toHaveTextContent('R$200')

    await user.click(screen.getByTestId('compare-mode-year_ago'))
    await waitFor(() =>
      expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'group', compare: 'year_ago', period: 'last_12m' }),
    )
    expect(url()).toContain('cmp=year_ago')

    await user.click(within(view).getByText('Casa'))
    await waitFor(() =>
      expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'category', group_id: 'g1', compare: 'year_ago', period: 'last_12m' }),
    )
  })

  it('Compare with dates of choice waits for the dates', async () => {
    renderWithProviders(ui, { route: '/reports?tab=explore&view=compare&cmp=custom' })
    expect(await screen.findByText(t('reports.explore.compare.pickHint'))).toBeInTheDocument()
    expect(api.reports.breakdown).not.toHaveBeenCalled()
  })
})

describe('Explore helpers', () => {
  it('says how few rows make 80% only when it is telling', () => {
    const many = Array.from({ length: 10 }, (_, i) => row({ id: `r${i}`, total: i < 2 ? '1000.00' : '10.00' }))
    expect(concentration(many)).toEqual({ n: 2, m: 10 })
    expect(concentration(many.slice(0, 4))).toBeNull()
    const flat = Array.from({ length: 8 }, (_, i) => row({ id: `f${i}`, total: '100.00' }))
    expect(concentration(flat)).toBeNull()
  })

  it('keeps the five biggest lines and sums the rest', () => {
    const rows = Array.from({ length: 7 }, (_, i) => row({ id: `r${i}`, label: `R${i}`, sparkline: Array(12).fill('1') }))
    const series = trendSeries(rows, 'Others')
    expect(series.map((s) => s.label)).toEqual(['R0', 'R1', 'R2', 'R3', 'R4', 'Others'])
    expect(series[5].values[0]).toBe(2)
  })

  it('steps the month colors from the biggest cell', () => {
    expect(heatStep(0, 100)).toBe(0)
    expect(heatStep(1, 100)).toBe(1)
    expect(heatStep(100, 100)).toBe(5)
    expect(heatStep(50, 100)).toBe(3)
  })
})
