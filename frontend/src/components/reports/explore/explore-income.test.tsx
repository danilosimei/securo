import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { useLocation } from 'react-router-dom'

import { ExploreTab } from '@/components/reports/explore/explore-tab'
import { drilldownTransactionParams, transactionsPageLink } from '@/components/reports/explore/drilldown'
import { renderWithProviders, t } from '@/test/utils'
import type { BreakdownParams, BreakdownResponse, BreakdownRow } from '@/lib/api'
import type { CategoryGroup } from '@/types'

const api = vi.hoisted(() => ({
  reports: { breakdown: vi.fn() },
  transactions: { list: vi.fn() },
  categories: { list: vi.fn().mockResolvedValue([]) },
  categoryGroups: { list: vi.fn() },
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

const GROUPS_LIST = [
  {
    id: 'g-casa', name: 'Moradia', icon: 'house', color: '#2a78d6', position: 0,
    categories: [
      { id: 'c-aluguel', name: 'Aluguel', icon: 'receipt', color: '#2a78d6' },
      { id: 'c-compra', name: 'Compra do apartamento', icon: 'building-2', color: '#2a78d6' },
    ],
  },
  {
    id: 'g-renda', name: 'Renda', icon: 'wallet', color: '#1baf7a', position: 1,
    categories: [{ id: 'c-salario', name: 'Salário', icon: 'briefcase', color: '#1baf7a' }],
  },
] as unknown as CategoryGroup[]

const GROUPS = [row({ id: 'g-casa', label: 'Moradia', total: '1000.00' })]
const CASA = [row({ id: 'c-aluguel', kind: 'category', label: 'Aluguel', total: '1000.00' })]
const INCOME = [
  row({ id: 'c-salario', kind: 'category', label: 'Salário', total: '5000.00', previous_total: '4000.00', change: 0.25 }),
  row({ id: null, kind: 'category', label: 'A conferir', total: '300.00', previous_total: '0.00', change: null }),
]
const PAYERS = [row({ id: 'FULANO', kind: 'merchant', label: 'Fulano de Tal', total: '300.00' })]

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
  api.categoryGroups.list.mockResolvedValue(GROUPS_LIST)
  api.reports.breakdown.mockImplementation(async (p: BreakdownParams) => {
    if (p.flow === 'income') {
      if (p.level === 'merchant') return resp(PAYERS, { total: '300.00' })
      return resp(INCOME, { total: '5300.00', previous_total: '4000.00' })
    }
    if (p.level === 'category') {
      return resp(CASA, {
        to_assets: p.group_id === 'g-casa' ? [{ id: 'c-compra', label: 'Compra do apartamento', total: '250000.00' }] : [],
      })
    }
    return resp(GROUPS)
  })
  api.transactions.list.mockResolvedValue({
    items: [], total: 0, page: 1, limit: 50, pages: 1,
    summary: { income: 300, expense: 0, net: 300, excluded: 0, currency: 'BRL' },
  })
})

describe('Explore: Gasto | Renda', () => {
  it('opens on spending, and Renda lists income categories with the line to check', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore' })
    await inTable('Moradia')
    expect(screen.getByTestId('explore-flow-expense')).toHaveAttribute('aria-checked', 'true')
    expect(url()).not.toContain('flow=')

    await user.click(screen.getByTestId('explore-flow-income'))
    await waitFor(() => expect(url()).toContain('flow=income'))
    await waitFor(() =>
      expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'category', flow: 'income', period: 'last_12m' }),
    )
    expect(await inTable('A conferir')).toBeInTheDocument()
    expect(screen.getByTestId('explore-flow-income')).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByText(t('reports.explore.income.spending'))).toBeInTheDocument()
    expect(screen.getByTestId('explore-total')).toHaveTextContent('R$5,300.00')
    // More money received is good news: a green pill.
    expect(screen.getByTestId('delta-pill')).toHaveAttribute('data-tone', 'good')
    // The first level of income has no way back to show.
    expect(screen.queryByRole('navigation', { name: t('reports.explore.breadcrumb') })).not.toBeInTheDocument()
    // The controls stay where they were.
    expect(screen.getByTestId('explore-view-group')).toBeInTheDocument()
    expect(screen.getByTestId('explore-chart-bars')).toBeInTheDocument()
  })

  it('goes from the line to check to who paid, then to their credits', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore&flow=income' })
    await user.click(await inTable('A conferir'))
    await waitFor(() =>
      expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'merchant', category_id: 'none', flow: 'income', period: 'last_12m' }),
    )
    expect(url()).toContain('category=none')
    const table = await screen.findByTestId('explore-table')
    expect(within(table).getByText(t('reports.explore.income.col.merchant'))).toBeInTheDocument()
    // The name the row came with (the backend's).
    expect(screen.getByRole('navigation', { name: t('reports.explore.breadcrumb') })).toHaveTextContent('A conferir')

    await user.click(await inTable('Fulano de Tal'))
    expect(await screen.findByTestId('explore-store-title')).toHaveTextContent('Fulano de Tal')
    await waitFor(() =>
      expect(api.transactions.list).toHaveBeenCalledWith(
        expect.objectContaining({ merchant_key: 'FULANO', uncategorized: true, type: 'credit' }),
      ),
    )
    expect(screen.getByRole('button', { name: t('reports.explore.income.backToCategory', { category: 'A conferir' }) })).toBeInTheDocument()
  })

  it('Month shows income categories by month, without groups', async () => {
    renderWithProviders(ui, { route: '/reports?tab=explore&flow=income&view=month' })
    const matrix = await screen.findByTestId('month-matrix')
    expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'category', flow: 'income', period: 'last_12m' })
    expect(matrix).toHaveTextContent(t('reports.explore.income.month.legend'))
    expect(screen.queryByTestId('month-mode-groups')).not.toBeInTheDocument()
  })

  it('Compare compares income categories and says what was received', async () => {
    renderWithProviders(ui, { route: '/reports?tab=explore&flow=income&view=compare' })
    await screen.findByTestId('compare-view')
    await waitFor(() =>
      expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'category', flow: 'income', compare: 'previous', period: 'last_12m' }),
    )
    const headline = await screen.findByTestId('report-headline')
    expect(headline).toHaveTextContent('R$1,300')
    const received = t('reports.explore.income.compare.answerMore', { value: '§', b: '', change: '' }).split('§')[0].trim()
    expect(headline).toHaveTextContent(received)
  })

  it('switching back to spending drops what was open on the income side', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore&flow=income&category=none' })
    await screen.findByTestId('explore-table')
    await user.click(screen.getByTestId('explore-flow-expense'))
    await waitFor(() => expect(url()).not.toContain('flow='))
    expect(url()).not.toContain('category=')
    await waitFor(() => expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'group', period: 'last_12m' }))
  })
})

describe('Explore: money that goes to the assets', () => {
  it('shows the group asset categories apart, with the project total', async () => {
    renderWithProviders(ui, { route: '/reports?tab=explore&group=g-casa' })
    const section = await screen.findByTestId('explore-to-assets')
    expect(section).toHaveTextContent(t('reports.explore.assets.title'))
    const rows = within(section).getAllByTestId('explore-to-assets-row')
    expect(rows).toHaveLength(1)
    expect(rows[0]).toHaveTextContent('Compra do apartamento')
    expect(rows[0]).toHaveTextContent('R$250,000.00')
    // Spending of the group (1.000) + what went to the assets (250.000).
    expect(within(section).getByTestId('explore-project-total')).toHaveTextContent('R$251,000.00')
    // Not added to the spending.
    expect(screen.getByTestId('explore-total')).toHaveTextContent('R$1,000.00')
    expect(within(rows[0]).getByTestId('category-glyph')).toHaveClass('lucide-building-2')
  })

  it('shows nothing when the group has no such category', async () => {
    renderWithProviders(ui, { route: '/reports?tab=explore&group=g-other' })
    await inTable('Aluguel')
    expect(screen.queryByTestId('explore-to-assets')).not.toBeInTheDocument()
  })
})

describe('Explore: icons', () => {
  it('puts the group icon, in the group color and with no background, before the name', async () => {
    renderWithProviders(ui, { route: '/reports?tab=explore' })
    const name = await inTable('Moradia')
    const glyph = within(name.closest('tr') as HTMLElement).getByTestId('category-glyph')
    expect(glyph).toHaveClass('lucide-house')
    expect(glyph).toHaveStyle({ color: '#2a78d6' })
    const bar = (await screen.findAllByTestId('explore-bar'))[0]
    expect(within(bar).getByTestId('category-glyph')).toHaveClass('lucide-house')
  })

  it('a category wears its own icon in its group color; a store has none', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore&flow=income' })
    const salario = (await inTable('Salário')).closest('tr') as HTMLElement
    const glyph = within(salario).getByTestId('category-glyph')
    expect(glyph).toHaveClass('lucide-briefcase')
    expect(glyph).toHaveStyle({ color: '#1baf7a' })

    await user.click(await inTable('A conferir'))
    const payer = (await inTable('Fulano de Tal')).closest('tr') as HTMLElement
    expect(within(payer).queryByTestId('category-glyph')).not.toBeInTheDocument()
  })
})

describe('Explore drilldown on the income side', () => {
  const range = { from: '2025-10-01', to: '2026-09-30' }

  it('lists only the credits of a payer without a category or across all of them', () => {
    expect(drilldownTransactionParams({ range, categoryId: 'none', merchantKey: 'k', page: 1, flow: 'income' })).toMatchObject({
      uncategorized: true,
      type: 'credit',
    })
    expect(drilldownTransactionParams({ range, categoryId: 'all', merchantKey: 'k', page: 1, flow: 'income' })).toMatchObject({
      type: 'credit',
    })
    // An income category: its debits give money back, so they stay.
    expect(drilldownTransactionParams({ range, categoryId: 'c1', merchantKey: 'k', page: 1, flow: 'income' })).not.toHaveProperty('type')
    expect(drilldownTransactionParams({ range, categoryId: 'none', merchantKey: 'k', page: 1 })).not.toHaveProperty('type')
    expect(transactionsPageLink({ range, categoryId: 'none', merchantKey: 'k', flow: 'income' })).toContain('type=credit')
  })
})
