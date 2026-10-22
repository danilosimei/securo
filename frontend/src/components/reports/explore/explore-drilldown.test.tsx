import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { useLocation, useNavigate } from 'react-router-dom'

import { ExploreTab } from '@/components/reports/explore/explore-tab'
import { renderWithProviders, t } from '@/test/utils'
import type { BreakdownParams, BreakdownResponse, BreakdownRow } from '@/lib/api'

const api = vi.hoisted(() => ({
  reports: { breakdown: vi.fn() },
  transactions: { list: vi.fn(), update: vi.fn(), delete: vi.fn(), unlinkTransfer: vi.fn() },
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
vi.mock('@/components/transaction-dialog', () => ({
  TransactionDialog: ({
    open,
    transaction,
    onSave,
  }: {
    open: boolean
    transaction: { id: string; description: string } | null
    onSave: (d: object) => void
  }) =>
    open ? (
      <div role="dialog">
        {transaction?.description}
        <button type="button" onClick={() => onSave({ description: 'novo' })}>
          save-tx
        </button>
      </div>
    ) : null,
}))

const SPARK = Array.from({ length: 12 }, () => '10.00')
const row = (over: Partial<BreakdownRow>): BreakdownRow => ({
  id: 'x', label: 'X', kind: 'group', total: '100.00', share: 0.5, previous_total: '0.00', change: null, count: 3, sparkline: SPARK, ...over,
})
const PERIOD = { from: '2025-10-01', to: '2026-09-30', partial: false, previous_from: '2024-10-01', previous_to: '2025-09-30' }
const resp = (rows: BreakdownRow[]): BreakdownResponse => ({
  period: PERIOD, currency: 'BRL', total: '200.00', previous_total: '100.00', rows, unconverted_count: 0,
})

const GROUPS = [
  row({ id: 'g1', label: 'Carro' }),
  row({ id: null, label: 'Sem grupo' }),
  row({ id: null, label: 'Sem categoria' }),
  row({ id: 'c9', kind: 'category', label: 'Estornos', total: '-20.00' }),
]
const CATEGORIES = [row({ id: 'c1', kind: 'category', label: 'Financiamento' })]
const MERCHANTS = [
  row({ id: 'stellantis', kind: 'merchant', label: 'Stellantis' }),
  row({ id: 'outra', kind: 'merchant', label: 'Outra' }),
]

function tx(i: number) {
  return {
    id: `t${i}`, description: `Parcela ${i}`, amount: 50, currency: 'BRL', date: '2026-03-10', type: 'debit',
    status: 'posted', source: 'manual', account_id: 'a1',
  }
}

function Probe() {
  const location = useLocation()
  const navigate = useNavigate()
  return (
    <>
      <output data-testid="url">{location.search}</output>
      <button type="button" onClick={() => navigate(-1)}>
        browser-back
      </button>
    </>
  )
}
const ui = (
  <>
    <ExploreTab />
    <Probe />
  </>
)
const url = () => screen.getByTestId('url').textContent
const table = () => within(screen.getByTestId('explore-table'))
const rowOf = (text: string) => table().getByText(text).closest('tr') as HTMLElement
/** Waits for `text` in the table (the bars above list the same names). */
const inTable = async (text: string) => within(await screen.findByTestId('explore-table')).findByText(text)

beforeEach(() => {
  vi.clearAllMocks()
  api.reports.breakdown.mockImplementation(async (p: BreakdownParams) =>
    resp(p.level === 'group' ? GROUPS : p.level === 'category' ? CATEGORIES : MERCHANTS),
  )
  api.transactions.list.mockResolvedValue({
    items: [tx(1), tx(2)], total: 120, page: 1, limit: 50, pages: 3,
    summary: { income: 0, expense: 6000, net: -6000, excluded: 0, currency: 'BRL' },
  })
})

describe('Explore drill-down', () => {
  it('goes group, category, store, calling the API with the right filters and updating the URL', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore&period=last_12m' })

    await inTable('Carro')
    await user.click(rowOf('Carro'))
    expect(await inTable('Financiamento')).toBeInTheDocument()
    expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'category', group_id: 'g1', period: 'last_12m' })
    expect(url()).toContain('group=g1')

    await user.click(rowOf('Financiamento'))
    expect(await inTable('Stellantis')).toBeInTheDocument()
    expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'merchant', category_id: 'c1', period: 'last_12m' })

    await user.click(rowOf('Stellantis'))
    expect(url()).toContain('merchant=stellantis')
    const nav = screen.getByRole('navigation', { name: t('reports.explore.breadcrumb') })
    expect(within(nav).getByText('Carro')).toBeInTheDocument()
    expect(within(nav).getByText('Financiamento')).toBeInTheDocument()
    expect(within(nav).getByText('Stellantis')).toBeInTheDocument()
  })

  it('with a store open, shows only the store and a way back to the stores of its category', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore' })
    await user.click(await inTable('Carro'))
    await user.click(await inTable('Financiamento'))
    await user.click(await inTable('Stellantis'))

    expect(await screen.findByTestId('explore-store-title')).toHaveTextContent('Stellantis · Financiamento')
    expect(screen.getByTestId('explore-store')).toHaveTextContent(t('reports.explore.storeCount', { count: 3 }))
    // The other stores and the chart are gone while the store is open.
    expect(screen.queryByTestId('explore-table')).not.toBeInTheDocument()
    expect(screen.queryByTestId('explore-chart')).not.toBeInTheDocument()
    expect(screen.queryByText('Outra')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('reports.explore.backToCategory', { category: 'Financiamento' }) }))
    expect(await inTable('Outra')).toBeInTheDocument()
    expect(url()).not.toContain('merchant=')
  })

  it('opens a row with Enter', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore' })
    await inTable('Carro')
    rowOf('Carro').focus()
    await user.keyboard('{Enter}')
    expect(await inTable('Financiamento')).toBeInTheDocument()
  })

  it('climbs a level with the browser Back', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore' })
    await inTable('Carro')
    await user.click(rowOf('Carro'))
    await inTable('Financiamento')

    await user.click(screen.getByRole('button', { name: 'browser-back' }))

    expect(await inTable('Carro')).toBeInTheDocument()
    expect(url()).not.toContain('group=')
  })

  it('climbs with the breadcrumb', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore' })
    await inTable('Carro')
    await user.click(rowOf('Carro'))
    await inTable('Financiamento')
    await user.click(rowOf('Financiamento'))
    await inTable('Stellantis')
    await user.click(rowOf('Stellantis'))
    await screen.findByTestId('explore-store')

    const nav = screen.getByRole('navigation', { name: t('reports.explore.breadcrumb') })
    await user.click(within(nav).getByRole('button', { name: 'Carro' }))
    expect(await inTable('Financiamento')).toBeInTheDocument()
    expect(url()).toContain('group=g1')
    expect(url()).not.toContain('category=')

    await user.click(within(screen.getByRole('navigation')).getByRole('button', { name: t('reports.explore.root') }))
    expect(await inTable('Sem grupo')).toBeInTheDocument()
    expect(url()).not.toContain('group=')
  })

  it('sends the no-category row straight to the stores', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore' })
    await inTable('Carro')
    await user.click(rowOf('Sem categoria'))
    await inTable('Stellantis')
    expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'merchant', category_id: 'none', period: 'last_12m' })
  })

  it('sends the no-group row to the categories without group', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore' })
    await inTable('Carro')
    await user.click(rowOf('Sem grupo'))
    await inTable('Financiamento')
    expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'category', group_id: 'none', period: 'last_12m' })
  })

  it('sends the refunds category straight to its stores', async () => {
    const { user } = renderWithProviders(ui, { route: '/reports?tab=explore' })
    await inTable('Carro')
    await user.click(rowOf('Estornos'))
    await inTable('Stellantis')
    expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'merchant', category_id: 'c9', period: 'last_12m' })
  })

  it('opens a link straight on a store without breaking the breadcrumb', async () => {
    renderWithProviders(ui, { route: '/reports?tab=explore&group=g1&category=c1&merchant=stellantis' })

    const nav = await screen.findByRole('navigation', { name: t('reports.explore.breadcrumb') })
    await waitFor(() => expect(within(nav).getByText('Stellantis')).toBeInTheDocument())
    expect(within(nav).getAllByText('…')).toHaveLength(2)
  })
})

describe('Explore store transactions', () => {
  const route = '/reports?tab=explore&period=last_12m&group=g1&category=c1&merchant=stellantis'

  it('lists the store transactions with the server total, asking with the drill-down filters', async () => {
    renderWithProviders(ui, { route })

    expect(await screen.findByText('Parcela 1')).toBeInTheDocument()
    expect(api.transactions.list).toHaveBeenCalledWith({
      from: '2025-10-01', to: '2026-09-30', category_id: 'c1', merchant_key: 'stellantis',
      status: 'posted', user_pnl_only: true, page: 1, limit: 50,
    })
    expect(screen.getByText(t('reports.explore.txTitle', { merchant: 'Stellantis', total: 120 }))).toBeInTheDocument()
    expect(screen.getByText('R$6,000.00')).toBeInTheDocument()
  })

  it('without a category, the store total is its spending only, as in the report (credits are income to check)', async () => {
    api.transactions.list.mockResolvedValue({
      items: [tx(1)], total: 2, page: 1, limit: 50, pages: 1,
      summary: { income: 500, expense: 900, net: -400, excluded: 0, currency: 'BRL' },
    })
    renderWithProviders(ui, { route: '/reports?tab=explore&period=last_12m&category=none&merchant=stellantis' })

    expect(await screen.findByText('R$900.00')).toBeInTheDocument()
  })

  it('pages with the arrows', async () => {
    const { user } = renderWithProviders(ui, { route })
    await screen.findByText('Parcela 1')
    expect(screen.getByRole('button', { name: t('reports.explore.prevPage') })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: t('reports.explore.nextPage') }))

    await waitFor(() => expect(api.transactions.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })))
  })

  it('links to Transactions with the same filters', async () => {
    renderWithProviders(ui, { route })
    const link = await screen.findByRole('link', { name: new RegExp(t('reports.explore.openInTransactions')) })
    expect(link).toHaveAttribute(
      'href',
      '/transactions?from=2025-10-01&to=2026-09-30&category_id=c1&merchant_key=stellantis&from_screen=reports',
    )
  })

  it('edits a transaction in the usual dialog and refreshes the report after saving', async () => {
    api.transactions.update.mockResolvedValue({})
    const { user, queryClient } = renderWithProviders(ui, { route })
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')

    await user.click(await screen.findByText('Parcela 1'))
    expect(screen.getByRole('dialog')).toHaveTextContent('Parcela 1')
    await user.click(screen.getByRole('button', { name: 'save-tx' }))

    await waitFor(() => expect(api.transactions.update).toHaveBeenCalledWith('t1', { description: 'novo' }))
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['reports'] }))
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['transactions'] })
  })
})
