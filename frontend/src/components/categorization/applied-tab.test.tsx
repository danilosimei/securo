import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import { AppliedTab } from '@/components/categorization/applied-tab'
import { AskProvider } from '@/components/categorization/ask-provider'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { renderWithProviders, t } from '@/test/utils'
import type { AppliedEvent } from '@/types'

const api = vi.hoisted(() => ({
  categorization: {
    applied: vi.fn(), undo: vi.fn(), choose: vi.fn(), similar: vi.fn(), applySimilar: vi.fn(),
    summary: vi.fn(), ask: vi.fn(),
  },
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
    memory_pick: false,
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
  api.categorization.similar.mockResolvedValue({ merchant_key: 'X', count: 0, transaction_ids: [], transactions: [] })
  api.categorization.applySimilar.mockResolvedValue({ applied: 2, event_ids: ['s1', 's2'], event_run_id: null })
})

const twoUberTrips = {
  merchant_key: 'UBER',
  count: 2,
  transaction_ids: ['u1', 'u2'],
  transactions: ['u1', 'u2'].map((id) => ({
    id,
    date: '2026-09-01',
    description: `UBER *OLD ${id}`,
    amount: '18.00',
    currency: 'BRL',
    type: 'debit',
    category_id: 'c3',
    category_source: 'memory',
  })),
}

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

  it('trocar o filtro mantém as linhas na tela, marcadas como carregando, até a nova página chegar', async () => {
    const { user } = await renderTab()
    api.categorization.applied.mockReturnValue(new Promise(() => {}))
    await user.click(screen.getByTestId('applied-confidence-low'))

    expect(screen.getByText('Today')).toBeInTheDocument()
    expect(screen.getByTestId('applied-list')).toHaveAttribute('aria-busy', 'true')
    expect(document.querySelector('[data-slot="skeleton"]')).toBeNull()
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
      transaction_ids: ['t4'],
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
    await user.type(await screen.findByPlaceholderText(t('categorization.create.searchOrCreate')), 'Carro')
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

describe('AppliedTab: confirmadas, suas e outras origens', () => {
  const pressed = (id: string) => screen.getByTestId(id).getAttribute('aria-pressed')

  it('clicar "Suas" pede source user e desmarca "IA"', async () => {
    const { user } = await renderTab()
    await user.click(screen.getByTestId('applied-source-ai'))
    await waitFor(() =>
      expect(api.categorization.applied).toHaveBeenLastCalledWith(expect.objectContaining({ source: 'ai' })),
    )
    await user.click(screen.getByTestId('applied-source-user'))
    await waitFor(() =>
      expect(api.categorization.applied).toHaveBeenLastCalledWith(expect.objectContaining({ source: 'user' })),
    )
    expect(pressed('applied-source-user')).toBe('true')
    expect(pressed('applied-source-ai')).toBe('false')
  })

  it('as vistas exclusivas se excluem, e IA/Memória/Regra limpam a vista', async () => {
    const { user } = await renderTab()
    await user.click(screen.getByTestId('applied-source-confirmed'))
    await user.click(screen.getByTestId('applied-source-other'))
    await waitFor(() =>
      expect(api.categorization.applied).toHaveBeenLastCalledWith(expect.objectContaining({ source: 'other' })),
    )
    expect(pressed('applied-source-confirmed')).toBe('false')
    await user.click(screen.getByTestId('applied-source-memory'))
    await waitFor(() =>
      expect(api.categorization.applied).toHaveBeenLastCalledWith(expect.objectContaining({ source: 'memory' })),
    )
    expect(pressed('applied-source-other')).toBe('false')
    // Clicking the active view again goes back to everything automatic.
    await user.click(screen.getByTestId('applied-source-confirmed'))
    await user.click(screen.getByTestId('applied-source-confirmed'))
    await waitFor(() =>
      expect(api.categorization.applied).toHaveBeenLastCalledWith(expect.objectContaining({ source: undefined })),
    )
  })

  it('em Suas e Outras origens a confiança não vale: desligada e fora do pedido', async () => {
    const { user } = await renderTab()
    await user.click(screen.getByTestId('applied-confidence-high'))
    await waitFor(() =>
      expect(api.categorization.applied).toHaveBeenLastCalledWith(expect.objectContaining({ min_confidence: 90 })),
    )
    await user.click(screen.getByTestId('applied-source-user'))
    await waitFor(() =>
      expect(api.categorization.applied).toHaveBeenLastCalledWith(
        expect.objectContaining({ source: 'user', min_confidence: undefined, max_confidence: undefined }),
      ),
    )
    expect(screen.getByTestId('applied-confidence-high')).toBeDisabled()
    expect(screen.getByTestId('applied-confidence-high').parentElement?.parentElement).toHaveAttribute(
      'title',
      t('categorization.applied.filters.confidenceNotHere'),
    )
    // Confirmadas are events: the confidence counts again, cleared (not the old "≥ 90").
    await user.click(screen.getByTestId('applied-source-confirmed'))
    expect(screen.getByTestId('applied-confidence-high')).toBeEnabled()
    await waitFor(() =>
      expect(api.categorization.applied).toHaveBeenLastCalledWith(
        expect.objectContaining({ source: 'confirmed', min_confidence: undefined, max_confidence: undefined }),
      ),
    )
  })

  it('trocar a confiança limpa a seleção, como os outros filtros', async () => {
    const { user } = await renderTab()
    const store = screen.getByRole('button', { name: /iFood/i })
    if (store.getAttribute('aria-expanded') !== 'true') await user.click(store)
    await user.click(screen.getByRole('checkbox', { name: /IFOOD \*PEDIDO 1/ }))
    expect(screen.getByRole('button', { name: 'Undo selected' })).toBeInTheDocument()
    await user.click(screen.getByTestId('applied-confidence-low'))
    expect(screen.queryByRole('button', { name: 'Undo selected' })).not.toBeInTheDocument()
  })

  it('corrigir em Suas mostra a categoria nova na hora, sem esperar a lista voltar', async () => {
    items = [
      ev(1, { event_id: null, run_id: null, source: 'user', confidence: null, reason: null,
        description: 'PADARIA DO ZE', merchant_key: 'PADARIA', store_name: 'Padaria do Zé' }),
    ]
    total = 1
    const { user } = await renderTab()
    await user.click(screen.getByTestId('applied-source-user'))
    const store = await screen.findByRole('button', { name: /Padaria do Zé/ })
    if (store.getAttribute('aria-expanded') !== 'true') await user.click(store)
    // The refetch after the fix never lands here: what shows is the patch.
    api.categorization.applied.mockImplementation(() => new Promise(() => {}))
    const row = screen.getByText('PADARIA DO ZE').closest('li') as HTMLElement
    await user.click(within(row).getByRole('button', { name: 'Restaurants' }))
    await user.click(await screen.findByRole('option', { name: /Groceries/ }))
    await waitFor(() => expect(api.transactions.update).toHaveBeenCalledWith('t1', { category_id: 'c2' }))
    const after = (await screen.findByText('PADARIA DO ZE')).closest('li') as HTMLElement
    expect(within(after).getByRole('button', { name: 'Groceries' })).toBeInTheDocument()
  })

  it('linha sem evento (event_id null) não tem caixa de seleção nem desfazer, mas corrige e pergunta à IA', async () => {
    items = [
      ev(1, {
        event_id: null, run_id: null, source: 'legacy', confidence: null, reason: null,
        description: 'PADARIA DO ZE', merchant_key: 'PADARIA', store_name: 'Padaria do Zé',
      }),
      ev(2, { event_id: null, run_id: null, source: 'legacy', confidence: null, reason: null,
        description: 'PADARIA DO ZE 2', merchant_key: 'PADARIA', store_name: 'Padaria do Zé' }),
    ]
    total = 2
    const { user } = renderWithProviders(
      <AskProvider>
        <AppliedTab />
      </AskProvider>,
    )
    await screen.findByText('Today')
    await user.click(screen.getByTestId('applied-source-user'))
    const store = await screen.findByRole('button', { name: /Padaria do Zé/ })
    if (store.getAttribute('aria-expanded') !== 'true') await user.click(store)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Undo/ })).not.toBeInTheDocument()
    const row = screen.getByText('PADARIA DO ZE').closest('li') as HTMLElement
    // We do not know what it was before: no "before" shown.
    expect(within(row).queryByText('No category')).not.toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Restaurants' })).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: t('categorization.ask.button') })).toBeInTheDocument()

    await user.click(within(row).getByRole('button', { name: 'Restaurants' }))
    await user.click(await screen.findByRole('option', { name: /Groceries/ }))
    await waitFor(() => expect(api.transactions.update).toHaveBeenCalledWith('t1', { category_id: 'c2' }))
  })

  it('confirmadas não se desfazem (não mudaram nada), mas se corrigem com a pergunta das parecidas', async () => {
    items = [
      ev(1, { previous_category_id: 'c1', new_category_id: 'c1' }),
      ev(2, { previous_category_id: 'c1', new_category_id: 'c1', source: 'memory' }),
    ]
    total = 2
    api.categorization.similar.mockResolvedValue({ ...twoUberTrips, merchant_key: 'IFOOD' })
    const { user } = await renderTab()
    await user.click(screen.getByTestId('applied-source-confirmed'))
    await waitFor(() =>
      expect(api.categorization.applied).toHaveBeenLastCalledWith(expect.objectContaining({ source: 'confirmed' })),
    )
    const store = screen.getByRole('button', { name: /iFood/i })
    if (store.getAttribute('aria-expanded') !== 'true') await user.click(store)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Undo/ })).not.toBeInTheDocument()

    const row = screen.getByText('IFOOD *PEDIDO 1').closest('li') as HTMLElement
    await user.click(within(row).getByRole('button', { name: 'Restaurants' }))
    await user.click(await screen.findByRole('option', { name: /Groceries/ }))
    await waitFor(() => expect(api.transactions.update).toHaveBeenCalledWith('t1', { category_id: 'c2' }))
    expect(await screen.findByText(t('categorization.similar.ask', { count: 2 }))).toBeInTheDocument()
  })

  it('em Suas, a confirmação da pessoa que ensinou a loja se desfaz', async () => {
    items = [
      ev(1, { source: 'user', run_id: null, previous_category_id: 'c1', new_category_id: 'c1', memory_pick: true }),
      ev(2, { source: 'user', run_id: null, previous_category_id: 'c1', new_category_id: 'c1' }),
    ]
    total = 2
    const { user } = await renderTab()
    await user.click(screen.getByTestId('applied-source-user'))
    await waitFor(() =>
      expect(api.categorization.applied).toHaveBeenLastCalledWith(expect.objectContaining({ source: 'user' })),
    )
    const store = await screen.findByRole('button', { name: /iFood/i })
    if (store.getAttribute('aria-expanded') !== 'true') await user.click(store)
    expect(screen.getByRole('checkbox', { name: /IFOOD \*PEDIDO 1/ })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: /IFOOD \*PEDIDO 2/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: /IFOOD \*PEDIDO 1/ }))
    await user.click(screen.getByRole('button', { name: 'Undo selected' }))
    await waitFor(() => expect(api.categorization.undo).toHaveBeenCalledWith({ event_ids: ['e1'] }))
  })

  it('o título e a dica dizem que vista é', async () => {
    const { user } = await renderTab()
    await user.click(screen.getByTestId('applied-source-user'))
    expect(
      await screen.findByText(
        t('categorization.applied.viewTitle', { view: t('categorization.applied.filters.user'), count: 4 }),
      ),
    ).toBeInTheDocument()
    expect(screen.getByText(t('categorization.applied.viewHint.user'))).toBeInTheDocument()
  })

  it('criar categoria que não mudou a linha (já editada) não pergunta pelas parecidas', async () => {
    api.categorization.choose.mockResolvedValue({
      category_id: 'c9', created_group: false, created_category: true, applied: 0, event_ids: [], transaction_ids: [],
    })
    const { user } = renderWithProviders(
      <WorkspaceContext.Provider value={{ canWrite: true } as unknown as React.ContextType<typeof WorkspaceContext>}>
        <AppliedTab />
      </WorkspaceContext.Provider>,
    )
    await screen.findByText('Today')
    const store = screen.getByRole('button', { name: /Uber/i })
    if (store.getAttribute('aria-expanded') !== 'true') await user.click(store)
    const row = screen.getByText('UBER *TRIP').closest('li') as HTMLElement
    await user.click(within(row).getByRole('button', { name: 'Rides' }))
    await user.type(await screen.findByPlaceholderText(t('categorization.create.searchOrCreate')), 'Carro')
    await user.click(screen.getByText(t('categorization.create.inGroup', { name: 'Carro', group: 'Transport' })))
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith(t('categorization.applied.skipped', { count: 1 })))
    expect(api.categorization.similar).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalledWith(t('categorization.applied.corrected'))
  })
})

describe('AppliedTab: parecidas da loja', () => {
  const ask = t('categorization.similar.ask', { count: 2 })

  async function fixUber(user: Awaited<ReturnType<typeof renderTab>>['user']) {
    const store = screen.getByRole('button', { name: /Uber/i })
    if (store.getAttribute('aria-expanded') !== 'true') await user.click(store)
    const row = screen.getByText('UBER *TRIP').closest('li') as HTMLElement
    await user.click(within(row).getByRole('button', { name: 'Rides' }))
    await user.click(await screen.findByRole('option', { name: /Groceries/ }))
  }

  it('depois de corrigir uma linha, a barra com o nome da loja pergunta pelas parecidas', async () => {
    api.categorization.similar.mockResolvedValue(twoUberTrips)
    const { user } = await renderTab()
    await fixUber(user)

    const bar = (await screen.findByText(ask)).closest('[data-similar-prompt]') as HTMLElement
    expect(within(bar).getByText(t('categorization.similar.store', { store: 'Uber' }))).toBeInTheDocument()
    expect(api.categorization.similar).toHaveBeenCalledWith('t4', { excludeRule: true })
    // The corrected row has left the list; the question stays at the top.
    await waitFor(() => expect(screen.queryByText('UBER *TRIP')).not.toBeInTheDocument())

    await user.click(within(bar).getByRole('button', { name: t('categorization.similar.apply', { count: 2 }) }))
    expect(api.categorization.applySimilar).toHaveBeenCalledWith('t4', {
      excludeRule: true,
      transactionIds: ['u1', 'u2'],
    })
    await waitFor(() => expect(screen.queryByText(ask)).not.toBeInTheDocument())
  })

  it('"Não" fecha a barra sem aplicar', async () => {
    api.categorization.similar.mockResolvedValue(twoUberTrips)
    const { user } = await renderTab()
    await fixUber(user)
    await user.click(await screen.findByRole('button', { name: t('categorization.similar.no') }))
    expect(screen.queryByText(ask)).not.toBeInTheDocument()
    expect(api.categorization.applySimilar).not.toHaveBeenCalled()
  })

  it('sem parecidas nenhuma barra aparece', async () => {
    const { user } = await renderTab()
    await fixUber(user)
    await waitFor(() => expect(api.categorization.similar).toHaveBeenCalledWith('t4', { excludeRule: true }))
    expect(document.querySelector('[data-similar-prompt]')).toBeNull()
  })
})

describe('AppliedTab: barra das parecidas, bordas', () => {
  it('uma correção nova troca a barra pela da outra loja', async () => {
    api.categorization.similar.mockImplementation(async (id: string) => ({
      ...twoUberTrips,
      merchant_key: id === 't4' ? 'UBER' : 'IFOOD',
    }))
    const { user } = await renderTab()
    const uber = screen.getByRole('button', { name: /Uber/i })
    if (uber.getAttribute('aria-expanded') !== 'true') await user.click(uber)
    const trip = screen.getByText('UBER *TRIP').closest('li') as HTMLElement
    await user.click(within(trip).getByRole('button', { name: 'Rides' }))
    await user.click(await screen.findByRole('option', { name: /Groceries/ }))
    expect(await screen.findByText(t('categorization.similar.store', { store: 'Uber' }))).toBeInTheDocument()

    const ifood = screen.getByRole('button', { name: /iFood/i })
    if (ifood.getAttribute('aria-expanded') !== 'true') await user.click(ifood)
    const order = screen.getByText('IFOOD *PEDIDO 1').closest('li') as HTMLElement
    await user.click(within(order).getByRole('button', { name: 'Restaurants' }))
    await user.click(await screen.findByRole('option', { name: /Groceries/ }))

    expect(await screen.findByText(t('categorization.similar.store', { store: 'iFood' }))).toBeInTheDocument()
    expect(screen.queryByText(t('categorization.similar.store', { store: 'Uber' }))).not.toBeInTheDocument()
    expect(document.querySelectorAll('[data-similar-prompt]')).toHaveLength(1)
    expect(api.categorization.similar).toHaveBeenLastCalledWith('t1', { excludeRule: true })
  })

  it('a barra fica presa no rodapé da tela, junto da barra de seleção', async () => {
    api.categorization.similar.mockResolvedValue(twoUberTrips)
    const { user } = await renderTab()
    const store = screen.getByRole('button', { name: /Uber/i })
    if (store.getAttribute('aria-expanded') !== 'true') await user.click(store)
    const trip = screen.getByText('UBER *TRIP').closest('li') as HTMLElement
    await user.click(within(trip).getByRole('button', { name: 'Rides' }))
    await user.click(await screen.findByRole('option', { name: /Groceries/ }))
    const bar = (await screen.findByText(t('categorization.similar.ask', { count: 2 }))).closest(
      '[data-similar-prompt]',
    ) as HTMLElement
    expect(bar.closest('.fixed')).not.toBeNull()
  })

  it('o conteúdo deixa a folga da altura real do rodapé (barras empilhadas no celular)', async () => {
    api.categorization.similar.mockResolvedValue(twoUberTrips)
    const original = HTMLElement.prototype.getBoundingClientRect
    const spy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return this.hasAttribute('data-applied-footer')
        ? DOMRect.fromRect({ width: 360, height: 230 })
        : original.call(this)
    })
    try {
      const { user } = await renderTab()
      const store = screen.getByRole('button', { name: /Uber/i })
      if (store.getAttribute('aria-expanded') !== 'true') await user.click(store)
      const trip = screen.getByText('UBER *TRIP').closest('li') as HTMLElement
      await user.click(within(trip).getByRole('button', { name: 'Rides' }))
      await user.click(await screen.findByRole('option', { name: /Groceries/ }))
      await screen.findByText(t('categorization.similar.ask', { count: 2 }))
      await waitFor(() => expect(screen.getByTestId('categorization-applied').style.paddingBottom).toBe('246px'))
    } finally {
      spy.mockRestore()
    }
  })
})

describe('AppliedTab: perguntar à IA', () => {
  const summary = (overrides: Record<string, unknown> = {}) => ({
    pending_groups: 0, pending_transactions: 0, applied_last_7d: 4, last_run: null,
    ai_available: true, ai_enabled: true, month_cost_usd: '0', monthly_cap_usd: '2', ...overrides,
  })

  async function renderAsk() {
    const utils = renderWithProviders(
      <AskProvider>
        <AppliedTab />
      </AskProvider>,
    )
    await screen.findByText('Today')
    return utils
  }

  async function uberRow(user: ReturnType<typeof renderWithProviders>['user']) {
    const store = screen.getByRole('button', { name: /Uber/i })
    if (store.getAttribute('aria-expanded') !== 'true') await user.click(store)
    return screen.getByText('UBER *TRIP').closest('li') as HTMLElement
  }

  beforeEach(() => {
    api.categorization.summary.mockResolvedValue(summary())
    api.categorization.ask.mockResolvedValue({
      reply: 'Corrida de app.',
      suggestion: { category_id: 'c3', new_category: null, group_exists: true, confidence: 80, applies_to: 1 },
      note: null,
      assistant_turn: '{"reply":"Corrida de app."}',
      person_messages_left: 29,
    })
    api.categorization.choose.mockResolvedValue({
      category_id: 'c2', created_group: false, created_category: false, applied: 1, event_ids: ['ch1'],
      transaction_ids: ['t4'],
    })
  })

  it('a linha pergunta só sobre a própria transação e aceitar corrige com a barra das parecidas', async () => {
    api.categorization.similar.mockResolvedValue(twoUberTrips)
    const { user } = await renderAsk()
    const r = await uberRow(user)
    const button = within(r).getByRole('button', { name: t('categorization.ask.button') })
    await waitFor(() => expect(button).toBeEnabled())
    await user.click(button)
    const dialog = await screen.findByRole('dialog', { name: t('categorization.ask.title', { name: 'Uber' }) })
    await user.type(within(dialog).getByRole('textbox'), 'corrida{Enter}')
    expect(api.categorization.ask).toHaveBeenCalledWith({
      merchant_key: 'UBER',
      transaction_ids: ['t4'],
      messages: [{ role: 'user', content: 'corrida' }],
    })
    const card = await within(dialog).findByTestId('ask-suggestion')
    // "Another category" in the card picks something else, with the same rows.
    await user.click(within(card).getByRole('button', { name: t('categorization.ask.other') }))
    await user.click(await screen.findByRole('option', { name: /Groceries/ }))
    expect(api.categorization.choose).toHaveBeenCalledWith({
      merchant_key: 'UBER',
      transaction_ids: ['t4'],
      category_id: 'c2',
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(screen.queryByText('UBER *TRIP')).not.toBeInTheDocument())
    expect(await screen.findByText(t('categorization.similar.ask', { count: 2 }))).toBeInTheDocument()
    expect(toast.success).toHaveBeenCalledWith(t('categorization.applied.corrected'))
  })

  async function acceptUberInChat(user: ReturnType<typeof renderWithProviders>['user']) {
    const r = await uberRow(user)
    const button = within(r).getByRole('button', { name: t('categorization.ask.button') })
    await waitFor(() => expect(button).toBeEnabled())
    await user.click(button)
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByRole('textbox'), 'corrida{Enter}')
    const card = await within(dialog).findByTestId('ask-suggestion')
    await user.click(within(card).getByRole('button', { name: t('categorization.ask.accept') }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  }

  it('a linha corrigida sai e, sem parecidas, o foco fica na aba (não no body)', async () => {
    const { user } = await renderAsk()
    await acceptUberInChat(user)
    await waitFor(() => expect(screen.queryByText('UBER *TRIP')).not.toBeInTheDocument())
    await waitFor(() => expect(screen.getByTestId('categorization-applied')).toHaveFocus())
  })

  it('parecidas que chegam depois do diálogo fechar ainda pegam o foco', async () => {
    let resolve: (v: typeof twoUberTrips) => void = () => {}
    api.categorization.similar.mockImplementation(
      () => new Promise((r) => {
        resolve = r
      }),
    )
    const { user } = await renderAsk()
    await acceptUberInChat(user)
    await waitFor(() => expect(screen.getByTestId('categorization-applied')).toHaveFocus())
    resolve(twoUberTrips)
    const apply = await screen.findByRole('button', { name: t('categorization.similar.apply', { count: 2 }) })
    await waitFor(() => expect(apply).toHaveFocus())
  })

  it('sem conexão o botão da linha fica desabilitado com o motivo', async () => {
    api.categorization.summary.mockResolvedValue(summary({ ai_available: false }))
    const { user } = await renderAsk()
    const r = await uberRow(user)
    const button = within(r).getByRole('button', { name: t('categorization.ask.button') })
    await waitFor(() => expect(button).toBeDisabled())
    expect(button).toHaveAttribute('title', t('categorization.ask.blocked.noConnection'))
  })

  it('quem só lê vê o botão desabilitado com blocked.readOnly', async () => {
    workspace.canWrite = false
    const { user } = await renderAsk()
    const r = await uberRow(user)
    const button = within(r).getByRole('button', { name: t('categorization.ask.button') })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('title', t('categorization.ask.blocked.readOnly'))
  })
})
