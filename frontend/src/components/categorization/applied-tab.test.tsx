import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import { AppliedTab } from '@/components/categorization/applied-tab'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { renderWithProviders, t } from '@/test/utils'
import type { AppliedEvent } from '@/types'

const api = vi.hoisted(() => ({
  categorization: { applied: vi.fn(), undo: vi.fn(), choose: vi.fn() },
  categories: { list: vi.fn() },
  categoryGroups: { list: vi.fn() },
  transactions: { update: vi.fn() },
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), dismiss: vi.fn() }))
const workspace = vi.hoisted(() => ({ canWrite: true, canManage: true }))

vi.mock('@/lib/api', () => api)
vi.mock('sonner', () => ({ toast }))
vi.mock('@/contexts/workspace-context', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/workspace-context')>()),
  useWorkspace: () => workspace,
}))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'BRL' } } }),
}))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

const cat = (id: string, name: string, group_id: string | null) => ({
  id, name, group_id, icon: 'circle-help', color: '#10b981', user_id: 'u',
  is_system: false, is_hidden: false, treat_as_transfer: false, is_ignored: false,
})
const restaurants = cat('c1', 'Restaurants', 'g1')
const groceries = cat('c2', 'Groceries', 'g1')
const rides = cat('c3', 'Rides', 'g2')
const groupsFixture = [
  { id: 'g1', name: 'Food', icon: 'x', color: '#000', user_id: 'u', position: 0, is_system: false, is_hidden: false, categories: [restaurants, groceries] },
  { id: 'g2', name: 'Transport', icon: 'x', color: '#000', user_id: 'u', position: 1, is_system: false, is_hidden: false, categories: [rides] },
]

const now = new Date()
const yesterday = new Date(now.getTime() - 24 * 3600 * 1000)

function ev(n: number, overrides: Partial<AppliedEvent> & { description?: string } = {}): AppliedEvent {
  const { description, ...rest } = overrides
  const uber = description?.startsWith('UBER')
  return {
    event_id: `e${n}`,
    run_id: 'run1',
    transaction: { id: `t${n}`, date: '2026-10-19', description: description ?? `IFOOD *PEDIDO ${n}`, amount: -30, currency: 'BRL' },
    source: 'ai',
    previous_category_id: null,
    new_category_id: 'c1',
    confidence: 95,
    reason: 'Food delivery app',
    created_at: now.toISOString(),
    merchant_key: uber ? 'UBER' : 'IFOOD',
    store_name: uber ? 'Uber' : 'iFood',
    ...rest,
  }
}

let items: AppliedEvent[] = []
let total = 0

beforeEach(() => {
  vi.clearAllMocks()
  workspace.canWrite = true
  items = [
    ev(1),
    ev(2, { previous_category_id: 'c2' }),
    ev(3, { source: 'memory', confidence: 85 }),
    ev(4, { description: 'UBER *TRIP', new_category_id: 'c3', created_at: yesterday.toISOString(), run_id: 'run2', source: 'rule', confidence: null, reason: null }),
  ]
  total = items.length
  api.categorization.applied.mockImplementation(async () => ({ items, total }))
  api.categorization.undo.mockResolvedValue({ restored: 2, skipped: 0 })
  api.categories.list.mockResolvedValue([restaurants, groceries, rides])
  api.categoryGroups.list.mockResolvedValue(groupsFixture)
  api.transactions.update.mockResolvedValue({})
})

async function renderTab() {
  const utils = renderWithProviders(<AppliedTab />)
  await screen.findByText('Today')
  return utils
}

describe('AppliedTab', () => {
  it('agrupa por dia e loja', async () => {
    await renderTab()
    expect(screen.getByText('Yesterday')).toBeInTheDocument()
    const store = screen.getByRole('button', { name: /iFood/i })
    expect(store).toHaveTextContent('3')
    expect(store).toHaveTextContent('Food › Restaurants')
    expect(screen.getByRole('button', { name: /Uber/i })).toBeInTheDocument()
  })

  it('mostra o antes e o depois com a descricao original', async () => {
    const { user } = await renderTab()
    const store = screen.getByRole('button', { name: /iFood/i })
    if (store.getAttribute('aria-expanded') !== 'true') await user.click(store)
    const first = screen.getByText('IFOOD *PEDIDO 1').closest('li') as HTMLElement
    expect(within(first).getByText('No category')).toBeInTheDocument()
    expect(within(first).getByText('95% sure')).toBeInTheDocument()
    expect(within(first).getByText('Food delivery app')).toBeInTheDocument()
    const second = screen.getByText('IFOOD *PEDIDO 2').closest('li') as HTMLElement
    expect(within(second).getByText('Groceries')).toBeInTheDocument()
  })

  it('filtro de origem refaz a query', async () => {
    const { user } = await renderTab()
    expect(api.categorization.applied).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0 }))
    await user.click(screen.getByTestId('applied-source-ai'))
    await waitFor(() =>
      expect(api.categorization.applied).toHaveBeenLastCalledWith(expect.objectContaining({ source: 'ai' })),
    )
    await user.click(screen.getByTestId('applied-confidence-low'))
    await waitFor(() =>
      expect(api.categorization.applied).toHaveBeenLastCalledWith(
        expect.objectContaining({ source: 'ai', max_confidence: 89 }),
      ),
    )
  })

  it('desfazer selecionadas chama undo com os ids e avisa que voltaram para Revisar', async () => {
    const { user } = await renderTab()
    const store = screen.getByRole('button', { name: /iFood/i })
    if (store.getAttribute('aria-expanded') !== 'true') await user.click(store)
    await user.click(screen.getByRole('checkbox', { name: /IFOOD \*PEDIDO 1/ }))
    await user.click(screen.getByRole('checkbox', { name: /IFOOD \*PEDIDO 2/ }))
    await user.click(screen.getByRole('button', { name: 'Undo selected' }))
    await waitFor(() => expect(api.categorization.undo).toHaveBeenCalledWith({ event_ids: ['e1', 'e2'] }))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('2 undone · back in Review', expect.anything()))
  })

  it('skipped aparece no toast', async () => {
    api.categorization.undo.mockResolvedValue({ restored: 1, skipped: 1 })
    const { user } = await renderTab()
    const store = screen.getByRole('button', { name: /iFood/i })
    if (store.getAttribute('aria-expanded') !== 'true') await user.click(store)
    await user.click(screen.getByRole('checkbox', { name: /IFOOD \*PEDIDO 1/ }))
    await user.click(screen.getByRole('checkbox', { name: /IFOOD \*PEDIDO 2/ }))
    await user.click(screen.getByRole('button', { name: 'Undo selected' }))
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        '1 undone · back in Review',
        expect.objectContaining({ description: '1 skipped because you had already edited it' }),
      ),
    )
  })

  it('desfazer este lote usa o run_id', async () => {
    const { user } = await renderTab()
    const day = screen.getByText('Today').closest('section') as HTMLElement
    await user.click(within(day).getByRole('button', { name: /Undo this batch \(3\)/ }))
    await waitFor(() => expect(api.categorization.undo).toHaveBeenCalledWith({ event_ids: ['e1', 'e2', 'e3'] }))
  })

  it('so avisa o ignorado quando nada foi restaurado', async () => {
    api.categorization.undo.mockResolvedValue({ restored: 0, skipped: 2 })
    const { user } = await renderTab()
    const day = screen.getByText('Today').closest('section') as HTMLElement
    await user.click(within(day).getByRole('button', { name: /Undo this batch/ }))
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith('2 skipped because you had already edited it'))
  })

  it('lojas diferentes com a mesma abertura nao se misturam', async () => {
    items = [
      ev(1, { description: 'COMPRA NO DEBITO PADARIA X', merchant_key: 'PADARIA X', store_name: 'Padaria X' }),
      ev(2, { description: 'COMPRA NO DEBITO MERCADO Y', merchant_key: 'MERCADO Y', store_name: 'Mercado Y' }),
    ]
    total = 2
    renderWithProviders(<AppliedTab />)
    expect(await screen.findByRole('button', { name: /Padaria X/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Mercado Y/ })).toBeInTheDocument()
  })

  it('carregar mais pede a proxima pagina', async () => {
    total = 120
    const { user } = await renderTab()
    expect(screen.getByText('Showing 4 of 120')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Load more' }))
    await waitFor(() =>
      expect(api.categorization.applied).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 4 })),
    )
  })

  it('corrigir a categoria na linha atualiza a transacao e ensina a memoria', async () => {
    const { user } = await renderTab()
    const store = screen.getByRole('button', { name: /Uber/i })
    if (store.getAttribute('aria-expanded') !== 'true') await user.click(store)
    const row = screen.getByText('UBER *TRIP').closest('li') as HTMLElement
    await user.click(within(row).getByRole('button', { name: 'Rides' }))
    await user.click(await screen.findByRole('option', { name: /Groceries/ }))
    await waitFor(() => expect(api.transactions.update).toHaveBeenCalledWith('t4', { category_id: 'c2' }))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Fixed — memory learned this'))
  })

  it('criar categoria na linha aplica so a essa transacao', async () => {
    api.categorization.choose.mockResolvedValue({
      category_id: 'c9', created_group: false, created_category: true, applied: 1, event_ids: ['e9'],
    })
    const { user, queryClient } = renderWithProviders(
      <WorkspaceContext.Provider value={{ canWrite: true } as unknown as React.ContextType<typeof WorkspaceContext>}>
        <AppliedTab />
      </WorkspaceContext.Provider>,
    )
    await screen.findByText('Today')
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const store = screen.getByRole('button', { name: /Uber/i })
    if (store.getAttribute('aria-expanded') !== 'true') await user.click(store)
    const row = screen.getByText('UBER *TRIP').closest('li') as HTMLElement
    await user.click(within(row).getByRole('button', { name: 'Rides' }))
    await user.type(await screen.findByPlaceholderText(t('transactions.searchCategory')), 'Carro')
    // The row is in Transport › Rides, so Transport comes first.
    await user.click(screen.getByText(t('categorization.create.inGroup', { name: 'Carro', group: 'Transport' })))

    await waitFor(() =>
      expect(api.categorization.choose).toHaveBeenCalledWith({
        merchant_key: 'UBER',
        transaction_ids: ['t4'],
        new_category: { group: 'Transport', name: 'Carro' },
      }),
    )
    expect(api.transactions.update).not.toHaveBeenCalled()
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Fixed — memory learned this'))
    await waitFor(() => expect(screen.queryByText('UBER *TRIP')).not.toBeInTheDocument())
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['categories'] })
  })

  it('visualizador so le: sem selecao nem desfazer', async () => {
    workspace.canWrite = false
    const { user } = await renderTab()
    const store = screen.getByRole('button', { name: /iFood/i })
    if (store.getAttribute('aria-expanded') !== 'true') await user.click(store)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Undo/ })).not.toBeInTheDocument()
    const row = screen.getByText('IFOOD *PEDIDO 1').closest('li') as HTMLElement
    expect(within(row).queryByRole('button')).not.toBeInTheDocument()
  })

  it('estado vazio', async () => {
    items = []
    total = 0
    renderWithProviders(<AppliedTab />)
    expect(await screen.findByText('Nothing was applied automatically yet')).toBeInTheDocument()
  })
})
