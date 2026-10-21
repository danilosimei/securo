import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, screen, waitFor, within } from '@testing-library/react'

import { WorkspaceContext } from '@/contexts/workspace-context'
import { ReviewTab } from '@/components/categorization/review-tab'
import { AskProvider } from '@/components/categorization/ask-provider'
import { renderWithProviders, t } from '@/test/utils'
import type { NewCategoryProposal, ReviewGroup, ReviewTransaction, SimilarResult, SimilarTransaction } from '@/types'

const api = vi.hoisted(() => ({
  categorization: {
    review: vi.fn(),
    accept: vi.fn(),
    dismiss: vi.fn(),
    createCategory: vi.fn(),
    choose: vi.fn(),
    undo: vi.fn(),
    setNote: vi.fn(),
    similar: vi.fn(),
    applySimilar: vi.fn(),
    summary: vi.fn(),
    ask: vi.fn(),
  },
  categories: { list: vi.fn() },
  categoryGroups: { list: vi.fn() },
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }))
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
  id,
  name,
  group_id,
  icon: 'circle-help',
  color: '#10b981',
  user_id: 'u',
  is_system: false,
  is_hidden: false,
  treat_as_transfer: false,
  is_ignored: false,
})
const restaurants = cat('c1', 'Restaurants', 'g1')
const groceries = cat('c2', 'Groceries', 'g1')
const rides = cat('c3', 'Rides', 'g2')
const groupsFixture = [
  { id: 'g1', name: 'Food', icon: 'x', color: '#000', user_id: 'u', position: 0, is_system: false, is_hidden: false, categories: [restaurants, groceries] },
  { id: 'g2', name: 'Transport', icon: 'x', color: '#000', user_id: 'u', position: 1, is_system: false, is_hidden: false, categories: [rides] },
]

function group(overrides: Partial<ReviewGroup> & { merchant_key: string }): ReviewGroup {
  return {
    clean_name: null,
    count: 1,
    total: '10.00',
    samples: [],
    suggestion: null,
    new_category: null,
    is_person: false,
    note: null,
    transaction_ids: [`${overrides.merchant_key}-tx`],
    transactions: [],
    ...overrides,
  }
}

const reviewTx = (id: string, original: string | null): ReviewTransaction => ({
  id,
  date: '2026-10-01',
  description: `desc ${id}`,
  original_description: original,
  amount: '25.50',
  currency: 'BRL',
  type: 'debit',
})

const ifood = group({
  merchant_key: 'IFOOD',
  clean_name: 'iFood',
  count: 12,
  total: '340.50',
  suggestion: { category_id: 'c1', alt_category_id: 'c2', confidence: 70, reason: 'Food delivery app' },
  transaction_ids: ['t1', 't2'],
  transactions: [reviewTx('t1', 'IFOOD *ORIGINAL t1'), reviewTx('t2', 'IFOOD *ORIGINAL t2')],
})
const uber = group({
  merchant_key: 'UBER',
  clean_name: 'Uber',
  count: 5,
  total: '120.00',
  suggestion: { category_id: 'c3', alt_category_id: null, confidence: 60, reason: 'Ride hailing' },
  transaction_ids: ['UBER-tx', 'UBER-tx2'],
  transactions: [reviewTx('UBER-tx', null)],
})
const maria = group({
  merchant_key: 'PIX:MARIA SILVA',
  clean_name: 'Maria Silva',
  count: 2,
  total: '50.00',
  is_person: true,
})

const noSimilar: SimilarResult = { merchant_key: 'X', count: 0, transaction_ids: [], transactions: [] }
const similarTx = (id: string): SimilarTransaction => ({
  id,
  date: '2026-09-01',
  description: `IFOOD *OLD ${id}`,
  amount: '30.00',
  currency: 'BRL',
  type: 'debit',
  category_id: 'c2',
  category_source: 'memory',
})
const twoSimilar: SimilarResult = {
  merchant_key: 'IFOOD',
  count: 2,
  transaction_ids: ['a', 'b'],
  transactions: [similarTx('a'), similarTx('b')],
}

let serverGroups: ReviewGroup[] = []
let proposals: NewCategoryProposal[] = []

beforeEach(() => {
  vi.clearAllMocks()
  workspace.canWrite = true
  serverGroups = [ifood, uber, maria]
  proposals = []
  api.categorization.review.mockImplementation(async () => ({
    groups: serverGroups,
    total: serverGroups.length,
    new_category_proposals: proposals,
  }))
  api.categories.list.mockResolvedValue([restaurants, groceries, rides])
  api.categoryGroups.list.mockResolvedValue(groupsFixture)
  api.categorization.accept.mockImplementation(async ({ merchant_key }: { merchant_key: string }) => {
    const found = serverGroups.find((g) => g.merchant_key === merchant_key)
    serverGroups = serverGroups.filter((g) => g.merchant_key !== merchant_key)
    return {
      applied: found?.count ?? 0,
      event_ids: [`${merchant_key}-e1`, `${merchant_key}-e2`],
      event_run_id: null,
    }
  })
  api.categorization.dismiss.mockImplementation(async (key: string) => {
    serverGroups = serverGroups.filter((g) => g.merchant_key !== key)
    return { dismissed: 1 }
  })
  api.categorization.undo.mockResolvedValue({ restored: 2, skipped: 0 })
  api.categorization.similar.mockResolvedValue(noSimilar)
  api.categorization.applySimilar.mockResolvedValue({ applied: 2, event_ids: ['s1', 's2'], event_run_id: null })
})

const row = (name: string) => screen.getByText(name).closest('[data-review-row]') as HTMLElement

async function renderTab() {
  const utils = renderWithProviders(<ReviewTab />)
  await screen.findByText('iFood')
  // The rows wait for the categories: the buttons are there with the first row.
  screen.getByRole('button', { name: /Food › Restaurants/ })
  return utils
}

describe('ReviewTab', () => {
  it('apagar a nota na linha chama setNote sem expandir a linha', async () => {
    serverGroups = [{ ...ifood, merchant_key: 'IFOOD', note: 'só às sextas' }, uber, maria]
    api.categorization.setNote.mockResolvedValue({ merchant_key: 'IFOOD', note: null })
    const { user } = renderWithProviders(
      <WorkspaceContext.Provider value={{ canWrite: true } as unknown as React.ContextType<typeof WorkspaceContext>}>
        <ReviewTab />
      </WorkspaceContext.Provider>,
    )
    await screen.findByText('iFood')
    const r = row('iFood')
    await user.click(within(r).getByRole('button', { name: t('categorization.note.delete') }))
    await waitFor(() =>
      expect(api.categorization.setNote).toHaveBeenCalledWith({ merchant_key: 'IFOOD', note: null }),
    )
    expect(within(r).getByRole('button', { name: /iFood/, expanded: false })).toBeInTheDocument()
  })

  it('sem permissão de escrita a linha não mostra o X da nota', async () => {
    serverGroups = [{ ...ifood, note: 'só às sextas' }, uber, maria]
    renderWithProviders(
      <WorkspaceContext.Provider value={{ canWrite: false } as unknown as React.ContextType<typeof WorkspaceContext>}>
        <ReviewTab />
      </WorkspaceContext.Provider>,
    )
    await screen.findByText('iFood')
    expect(within(row('iFood')).queryByRole('button', { name: t('categorization.note.delete') })).not.toBeInTheDocument()
  })

  it('mostra a nota da loja na linha', async () => {
    serverGroups = [{ ...ifood, note: 'só às sextas' }, uber, maria]
    await renderTab()
    expect(within(row('iFood')).getByText(/só às sextas/)).toBeInTheDocument()
    expect(within(row('Uber')).queryByText(/Note:/)).not.toBeInTheDocument()
  })

  it('aceitar palpite remove a linha e mostra toast com Desfazer', async () => {
    const { user } = await renderTab()

    await user.click(within(row('iFood')).getByRole('button', { name: /Food › Restaurants/ }))

    expect(api.categorization.accept).toHaveBeenCalledWith({
      merchant_key: 'IFOOD',
      category_id: 'c1',
      transaction_ids: ['t1', 't2'],
    })
    await waitFor(() => expect(screen.queryByText('iFood')).not.toBeInTheDocument())
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        t('categorization.review.applied', { count: 12 }),
        expect.objectContaining({
          action: expect.objectContaining({ label: t('common.undo') }),
        }),
      ),
    )
  })

  it('desfazer chama undo com os event_ids e a linha volta', async () => {
    const { user } = await renderTab()
    await user.click(within(row('iFood')).getByRole('button', { name: /Food › Restaurants/ }))
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    await waitFor(() => expect(screen.queryByText('iFood')).not.toBeInTheDocument())

    serverGroups = [ifood, ...serverGroups]
    const options = toast.success.mock.calls[0][1] as { action: { onClick: () => void } }
    await act(async () => options.action.onClick())

    expect(api.categorization.undo).toHaveBeenCalledWith({ event_ids: ['IFOOD-e1', 'IFOOD-e2'] })
    expect(await screen.findByText('iFood')).toBeInTheDocument()
  })

  it('erro ao aceitar devolve a linha e mostra o erro', async () => {
    api.categorization.accept.mockRejectedValueOnce({
      response: { status: 404, data: { detail: 'Category not found' } },
    })
    const { user } = await renderTab()
    await user.click(within(row('iFood')).getByRole('button', { name: /Food › Restaurants/ }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Category not found'))
    expect(screen.getByText('iFood')).toBeInTheDocument()
  })

  it('atalho 1 aceita o palpite da linha focada', async () => {
    const { user } = await renderTab()
    act(() => row('iFood').focus())
    await user.keyboard('{ArrowDown}')
    expect(row('Uber')).toHaveFocus()

    await user.keyboard('1')

    expect(api.categorization.accept).toHaveBeenCalledWith(
      expect.objectContaining({ merchant_key: 'UBER', category_id: 'c3' }),
    )
    // The next store takes the focus, so the next key press keeps going.
    await waitFor(() => expect(row('Maria Silva')).toHaveFocus())
  })

  it('atalho 2 aceita a alternativa e x ignora quando o toast fecha', async () => {
    const { user } = await renderTab()
    act(() => row('iFood').focus())
    await user.keyboard('2')
    expect(api.categorization.accept).toHaveBeenCalledWith(
      expect.objectContaining({ merchant_key: 'IFOOD', category_id: 'c2' }),
    )
    await waitFor(() => expect(row('Uber')).toHaveFocus())
    toast.success.mockClear()

    await user.keyboard('x')
    expect(screen.queryByText('Uber')).not.toBeInTheDocument()
    expect(row('Maria Silva')).toHaveFocus()
    // Nothing is sent while the toast can still take it back.
    expect(api.categorization.dismiss).not.toHaveBeenCalled()
    expect(toast.success).toHaveBeenCalledWith(
      t('categorization.review.dismissed', { name: 'Uber' }),
      expect.objectContaining({ action: expect.objectContaining({ label: t('common.undo') }) }),
    )

    const options = toast.success.mock.calls[0][1] as { onAutoClose: () => void; onDismiss: () => void }
    await act(async () => options.onAutoClose())
    expect(api.categorization.dismiss).toHaveBeenCalledWith('UBER')
    // A second close callback does not send it twice.
    await act(async () => options.onDismiss())
    expect(api.categorization.dismiss).toHaveBeenCalledTimes(1)
  })

  it('desfazer o ignorar devolve a linha sem chamar a API', async () => {
    const { user } = await renderTab()
    await user.click(within(row('Uber')).getByRole('button', { name: t('categorization.review.ignore') }))
    expect(screen.queryByText('Uber')).not.toBeInTheDocument()

    const options = toast.success.mock.calls[0][1] as {
      action: { onClick: () => void }
      onDismiss: () => void
    }
    act(() => options.action.onClick())
    expect(await screen.findByText('Uber')).toBeInTheDocument()
    // The toast closing afterwards must not send the undone answer.
    await act(async () => options.onDismiss())
    expect(api.categorization.dismiss).not.toHaveBeenCalled()
  })

  it('sair da aba envia o ignorar pendente', async () => {
    const { user, unmount } = await renderTab()
    await user.click(within(row('Uber')).getByRole('button', { name: t('categorization.review.ignore') }))
    expect(api.categorization.dismiss).not.toHaveBeenCalled()

    unmount()
    expect(api.categorization.dismiss).toHaveBeenCalledWith('UBER', { keepalive: true })
    expect(toast.dismiss).toHaveBeenCalled()
  })

  it('fechar a página envia o ignorar pendente com keepalive', async () => {
    const { user } = await renderTab()
    await user.click(within(row('Uber')).getByRole('button', { name: t('categorization.review.ignore') }))

    act(() => {
      window.dispatchEvent(new Event('pagehide'))
    })
    // keepalive: the request outlives the tab that is closing.
    expect(api.categorization.dismiss).toHaveBeenCalledWith('UBER', { keepalive: true })
    expect(api.categorization.dismiss).toHaveBeenCalledTimes(1)
  })

  it('Tab percorre os botões só da loja atual', async () => {
    const { user } = await renderTab()
    const tabbable = (el: HTMLElement) =>
      within(el)
        .getAllByRole('button')
        .filter((b) => b.tabIndex >= 0)
    // The current row's answers are reachable; another row's are not.
    expect(tabbable(row('iFood')).length).toBeGreaterThan(0)
    expect(tabbable(row('Uber'))).toEqual([])

    act(() => row('iFood').focus())
    await user.keyboard('{ArrowDown}')
    expect(row('Uber')).toHaveFocus()
    expect(tabbable(row('iFood'))).toEqual([])
    expect(tabbable(row('Uber')).length).toBeGreaterThan(0)
  })

  it('aceitar com o mouse leva o foco para a próxima loja', async () => {
    const { user } = await renderTab()
    await user.click(within(row('iFood')).getByRole('button', { name: /Food › Restaurants/ }))
    await waitFor(() => expect(row('Uber')).toHaveFocus())
  })

  it('Tab entra na lista pela loja atual', async () => {
    const { user } = await renderTab()
    act(() => screen.getByRole('button', { name: t('categorization.review.acceptAll') }).focus())
    await user.tab()
    expect(row('iFood')).toHaveFocus()
  })

  it('Enter expande e lista as transações da loja', async () => {
    const { user } = await renderTab()
    act(() => row('iFood').focus())
    await user.keyboard('{Enter}')

    expect(await screen.findByText('IFOOD *ORIGINAL t1')).toBeInTheDocument()
    expect(screen.getByText('IFOOD *ORIGINAL t2')).toBeInTheDocument()
    const disclosure = within(row('iFood')).getByRole('button', { name: 'iFood' })
    expect(disclosure).toHaveAttribute('aria-expanded', 'true')
    expect(document.getElementById(disclosure.getAttribute('aria-controls')!)).toHaveTextContent(
      'IFOOD *ORIGINAL t1',
    )
    expect(row('iFood')).not.toHaveAttribute('aria-expanded')
  })

  it('o nome da loja é um botão que abre e fecha', async () => {
    const { user } = await renderTab()
    const disclosure = within(row('Uber')).getByRole('button', { name: 'Uber' })
    await user.click(disclosure)
    expect(disclosure).toHaveAttribute('aria-expanded', 'true')
    // No original description: the current one.
    expect(await screen.findByText('desc UBER-tx')).toBeInTheDocument()
    // The rows the queue did not carry are counted, not fetched.
    expect(screen.getByText(t('categorization.review.more', { count: 1 }))).toBeInTheDocument()
    await user.click(disclosure)
    expect(disclosure).toHaveAttribute('aria-expanded', 'false')
  })

  it('atalhos não disparam dentro do campo de busca do seletor', async () => {
    const { user } = await renderTab()
    await user.click(within(row('iFood')).getByRole('button', { name: t('categorization.review.other') }))
    const search = await screen.findByPlaceholderText(t('transactions.searchCategory'))
    await user.type(search, '1x2')

    expect(api.categorization.accept).not.toHaveBeenCalled()
    expect(api.categorization.dismiss).not.toHaveBeenCalled()
  })

  it('"Outra…" aplica a categoria escolhida', async () => {
    const { user } = await renderTab()
    await user.click(within(row('Uber')).getByRole('button', { name: t('categorization.review.other') }))
    await user.click(await screen.findByRole('option', { name: /Groceries/ }))

    expect(api.categorization.accept).toHaveBeenCalledWith(
      expect.objectContaining({ merchant_key: 'UBER', category_id: 'c2' }),
    )
  })

  it('"Outra…" cria a categoria no grupo sugerido e aplica às pendentes da loja', async () => {
    api.categorization.choose.mockImplementation(async ({ merchant_key }: { merchant_key: string }) => {
      serverGroups = serverGroups.filter((g) => g.merchant_key !== merchant_key)
      return { category_id: 'c9', created_group: false, created_category: true, applied: 5, event_ids: ['UBER-e1'] }
    })
    const { user, queryClient } = renderWithProviders(
      <WorkspaceContext.Provider value={{ canWrite: true } as unknown as React.ContextType<typeof WorkspaceContext>}>
        <ReviewTab />
      </WorkspaceContext.Provider>,
    )
    await screen.findByText('Uber')
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    await user.click(within(row('Uber')).getByRole('button', { name: t('categorization.review.other') }))
    await user.type(await screen.findByPlaceholderText(t('transactions.searchCategory')), 'Carro')
    // Uber's suggestion is Transport › Rides, so Transport comes first.
    await user.click(screen.getByText(t('categorization.create.inGroup', { name: 'Carro', group: 'Transport' })))

    // No transaction_ids: the store's pending rows, whatever they are by now.
    await waitFor(() =>
      expect(api.categorization.choose).toHaveBeenCalledWith({
        merchant_key: 'UBER',
        new_category: { group: 'Transport', name: 'Carro' },
      }),
    )
    expect(api.categorization.accept).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByText('Uber')).not.toBeInTheDocument())
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        t('categorization.create.created'),
        expect.objectContaining({
          description: t('categorization.review.applied', { count: 5 }),
          action: expect.objectContaining({ label: t('common.undo') }),
        }),
      ),
    )
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['categories'] })
  })

  it('"Outra…" com erro ao criar devolve a linha', async () => {
    api.categorization.choose.mockRejectedValueOnce({
      response: { status: 409, data: { detail: { code: 'x', message: 'Name taken' } } },
    })
    const { user } = renderWithProviders(
      <WorkspaceContext.Provider value={{ canWrite: true } as unknown as React.ContextType<typeof WorkspaceContext>}>
        <ReviewTab />
      </WorkspaceContext.Provider>,
    )
    await screen.findByText('Uber')
    await user.click(within(row('Uber')).getByRole('button', { name: t('categorization.review.other') }))
    await user.type(await screen.findByPlaceholderText(t('transactions.searchCategory')), 'Carro')
    await user.click(screen.getByText(t('categorization.create.newGroup', { name: 'Carro' })))

    await waitFor(() =>
      expect(api.categorization.choose).toHaveBeenCalledWith({
        merchant_key: 'UBER',
        new_category: { group: 'Carro', name: 'Carro' },
      }),
    )
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(screen.getByText('Uber')).toBeInTheDocument()
  })

  it('aceitar todas pede confirmação e desfaz tudo de uma vez', async () => {
    // Hold the answers, to see the list while they are on their way.
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const answer = api.categorization.accept.getMockImplementation()!
    api.categorization.accept.mockImplementation(async (body: { merchant_key: string }) => {
      await gate
      return answer(body)
    })
    const { user } = await renderTab()
    await user.click(screen.getByRole('button', { name: t('categorization.review.acceptAll') }))
    expect(api.categorization.accept).not.toHaveBeenCalled()

    // Maria has no suggestion: only the two stores with one are counted.
    await user.click(screen.getByRole('button', { name: t('categorization.review.confirmAll', { count: 2 }) }))

    const applying = await screen.findByRole('button', { name: t('categorization.review.applying') })
    expect(applying).toBeDisabled()
    expect(screen.getByText('iFood')).toBeInTheDocument()
    expect(row('iFood')).toHaveAttribute('aria-busy', 'true')
    expect(screen.queryByText(t('categorization.review.empty'))).not.toBeInTheDocument()
    await act(async () => release())

    await waitFor(() => expect(api.categorization.accept).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1))
    expect(toast.success).toHaveBeenCalledWith(
      t('categorization.review.applied', { count: 17 }),
      expect.objectContaining({ action: expect.objectContaining({ label: t('common.undo') }) }),
    )
    await waitFor(() => expect(screen.queryByText('iFood')).not.toBeInTheDocument())
    expect(screen.queryByText('Uber')).not.toBeInTheDocument()
    expect(screen.getByText('Maria Silva')).toBeInTheDocument()

    const options = toast.success.mock.calls[0][1] as { action: { onClick: () => void } }
    await act(async () => options.action.onClick())
    expect(api.categorization.undo).toHaveBeenCalledTimes(1)
    const ids = api.categorization.undo.mock.calls[0][0].event_ids
    expect([...ids].sort()).toEqual(['IFOOD-e1', 'IFOOD-e2', 'UBER-e1', 'UBER-e2'])
  })

  it('aceitar todas com falha parcial: as que falharam voltam e o desfazer cobre só as aplicadas', async () => {
    const answer = api.categorization.accept.getMockImplementation()!
    api.categorization.accept.mockImplementation(async (body: { merchant_key: string }) => {
      if (body.merchant_key === 'UBER') {
        throw { response: { status: 404, data: { detail: 'Category not found' } } }
      }
      return answer(body)
    })
    const { user } = await renderTab()
    await user.click(screen.getByRole('button', { name: t('categorization.review.acceptAll') }))
    await user.click(screen.getByRole('button', { name: t('categorization.review.confirmAll', { count: 2 }) }))

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        t('categorization.review.partialFailure', { count: 1, error: 'Category not found' }),
      ),
    )
    expect(toast.success).toHaveBeenCalledWith(
      t('categorization.review.applied', { count: 12 }),
      expect.anything(),
    )
    await waitFor(() => expect(screen.queryByText('iFood')).not.toBeInTheDocument())
    expect(screen.getByText('Uber')).toBeInTheDocument()

    const options = toast.success.mock.calls[0][1] as { action: { onClick: () => void } }
    await act(async () => options.action.onClick())
    expect(api.categorization.undo).toHaveBeenCalledWith({ event_ids: ['IFOOD-e1', 'IFOOD-e2'] })
  })

  it('desfazer envia os event_ids em lotes de até 5000', async () => {
    const many = Array.from({ length: 5001 }, (_, i) => `e${i}`)
    api.categorization.accept.mockResolvedValueOnce({ applied: 5001, event_ids: many, event_run_id: null })
    const { user } = await renderTab()
    await user.click(within(row('iFood')).getByRole('button', { name: /Food › Restaurants/ }))
    await waitFor(() => expect(toast.success).toHaveBeenCalled())

    const options = toast.success.mock.calls[0][1] as { action: { onClick: () => void } }
    await act(async () => options.action.onClick())
    expect(api.categorization.undo).toHaveBeenCalledTimes(2)
    expect(api.categorization.undo.mock.calls[0][0].event_ids).toHaveLength(5000)
    expect(api.categorization.undo.mock.calls[1][0].event_ids).toEqual(['e5000'])
  })

  it('a confirmação de aceitar todas expira', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const { user } = await renderTab()
      await user.click(screen.getByRole('button', { name: t('categorization.review.acceptAll') }))
      expect(screen.getByRole('button', { name: t('categorization.review.confirmAll', { count: 2 }) })).toBeInTheDocument()
      act(() => vi.advanceTimersByTime(4100))
      expect(screen.getByRole('button', { name: t('categorization.review.acceptAll') })).toBeInTheDocument()
      expect(api.categorization.accept).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('pessoa mostra pergunta', async () => {
    await renderTab()
    expect(screen.getByText(t('categorization.review.whoIs', { name: 'Maria Silva' }))).toBeInTheDocument()
  })

  it('mostra quantidade, total e motivo da loja', async () => {
    await renderTab()
    const ifoodRow = row('iFood')
    expect(within(ifoodRow).getByText(/12 transactions/)).toBeInTheDocument()
    expect(within(ifoodRow).getByText(/340\.50/)).toBeInTheDocument()
    expect(within(ifoodRow).getByText('Food delivery app')).toBeInTheDocument()
  })

  it('viewer não vê botões nem usa atalhos', async () => {
    workspace.canWrite = false
    const { user } = renderWithProviders(<ReviewTab />)
    await screen.findByText('iFood')

    expect(screen.queryByRole('button', { name: t('categorization.review.acceptAll') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Food › Restaurants/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('categorization.review.ignore') })).not.toBeInTheDocument()

    act(() => row('iFood').focus())
    await user.keyboard('1x')
    expect(api.categorization.accept).not.toHaveBeenCalled()
    expect(api.categorization.dismiss).not.toHaveBeenCalled()
  })

  it('estado vazio', async () => {
    serverGroups = []
    renderWithProviders(<ReviewTab />)
    expect(await screen.findByText(t('categorization.review.empty'))).toBeInTheDocument()
    expect(screen.getByText(t('categorization.review.emptyHint'))).toBeInTheDocument()
  })

  it('proposta de categoria nova: criar e aplicar / agora não', async () => {
    proposals = [
      { group: 'Leisure', name: 'Games', merchant_keys: ['IFOOD', 'UBER'], count: 17, total: '460.50' },
    ]
    api.categorization.createCategory.mockResolvedValue({
      category_id: 'c9',
      applied: 17,
      event_ids: ['n1'],
    })
    const { user } = await renderTab()
    expect(screen.getByText(t('categorization.review.proposal', { path: 'Leisure › Games' }))).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('categorization.review.createAndApply') }))
    expect(api.categorization.createCategory).toHaveBeenCalledWith({
      group: 'Leisure',
      name: 'Games',
      merchant_keys: ['IFOOD', 'UBER'],
    })
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
  })

  it('"Agora não" esconde a proposta', async () => {
    proposals = [
      { group: 'Leisure', name: 'Games', merchant_keys: ['IFOOD'], count: 12, total: '340.50' },
    ]
    const { user } = await renderTab()
    const text = t('categorization.review.proposal', { path: 'Leisure › Games' })
    expect(screen.getByText(text)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('categorization.review.notNow') }))
    expect(screen.queryByText(text)).not.toBeInTheDocument()
    expect(api.categorization.createCategory).not.toHaveBeenCalled()
  })
})

describe('ReviewTab: parecidas da loja', () => {
  const ask = t('categorization.similar.ask', { count: 2 })
  const applyTwo = t('categorization.similar.apply', { count: 2 })

  beforeEach(() => {
    api.categorization.similar.mockImplementation(async (id: string) => (id === 't1' ? twoSimilar : noSimilar))
  })

  it('depois de aceitar, a linha fica com a pergunta mesmo após o refetch sem a loja; "Não" a remove', async () => {
    const { user, queryClient } = await renderTab()
    await user.click(within(row('iFood')).getByRole('button', { name: /Food › Restaurants/ }))

    expect(await within(row('iFood')).findByText(ask)).toBeInTheDocument()
    expect(api.categorization.similar).toHaveBeenCalledWith('t1', { excludeRule: true })
    // The answer's own toast (with undo) is out already.
    expect(toast.success).toHaveBeenCalledWith(
      t('categorization.review.applied', { count: 12 }),
      expect.objectContaining({ action: expect.objectContaining({ label: t('common.undo') }) }),
    )
    // The suggestion buttons gave way to the question.
    expect(within(row('iFood')).queryByRole('button', { name: /Food › Restaurants/ })).not.toBeInTheDocument()

    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ['categorization', 'review'] })
    })
    expect(within(row('iFood')).getByText(ask)).toBeInTheDocument()

    await user.click(within(row('iFood')).getByRole('button', { name: t('categorization.similar.no') }))
    await waitFor(() => expect(screen.queryByText('iFood')).not.toBeInTheDocument())
    expect(api.categorization.applySimilar).not.toHaveBeenCalled()
    await waitFor(() => expect(row('Uber')).toHaveFocus())
  })

  it('na linha respondida a legenda troca para Enter aplicar às parecidas · Esc não', async () => {
    const { user } = await renderTab()
    const legend = screen.getByTestId('review-shortcuts')
    expect(legend).toHaveTextContent(t('categorization.review.keys.accept'))
    act(() => row('iFood').focus())
    await user.keyboard('1')
    await within(row('iFood')).findByText(ask)
    expect(legend).toHaveTextContent(t('categorization.review.keys.applySimilar'))
    expect(legend).toHaveTextContent(t('categorization.review.keys.declineSimilar'))
    expect(legend).not.toHaveTextContent(t('categorization.review.keys.accept'))
    // The focus left the list: the legend goes back to the usual keys.
    act(() => row('iFood').blur())
    expect(legend).toHaveTextContent(t('categorization.review.keys.accept'))
  })

  it('pelo teclado: o foco fica na loja respondida e Enter aplica às N', async () => {
    const { user } = await renderTab()
    act(() => row('iFood').focus())
    await user.keyboard('1')
    await within(row('iFood')).findByText(ask)
    expect(row('iFood')).toHaveFocus()

    await user.keyboard('{Enter}')
    expect(api.categorization.applySimilar).toHaveBeenCalledWith('t1', {
      excludeRule: true,
      transactionIds: ['a', 'b'],
    })
    await waitFor(() => expect(screen.queryByText('iFood')).not.toBeInTheDocument())
    await waitFor(() => expect(row('Uber')).toHaveFocus())
    expect(toast.success).toHaveBeenCalledWith(
      t('categorization.similar.applied', { count: 2 }),
      expect.objectContaining({ action: expect.objectContaining({ label: t('common.undo') }) }),
    )
  })

  it('pelo teclado: Escape é o "Não"', async () => {
    const { user } = await renderTab()
    act(() => row('iFood').focus())
    await user.keyboard('1')
    await within(row('iFood')).findByText(ask)

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByText('iFood')).not.toBeInTheDocument())
    expect(api.categorization.applySimilar).not.toHaveBeenCalled()
    await waitFor(() => expect(row('Uber')).toHaveFocus())
  })

  it('a pergunta não trava as outras lojas', async () => {
    const { user } = await renderTab()
    await user.click(within(row('iFood')).getByRole('button', { name: /Food › Restaurants/ }))
    await within(row('iFood')).findByText(ask)

    // The answered row's own shortcuts are gone; the other rows still answer.
    act(() => row('iFood').focus())
    await user.keyboard('2')
    expect(api.categorization.accept).toHaveBeenCalledTimes(1)
    await user.click(within(row('Uber')).getByRole('button', { name: /Transport › Rides/ }))
    await waitFor(() =>
      expect(api.categorization.accept).toHaveBeenCalledWith(expect.objectContaining({ merchant_key: 'UBER' })),
    )
    await waitFor(() => expect(screen.queryByText('Uber')).not.toBeInTheDocument())
    expect(within(row('iFood')).getByText(ask)).toBeInTheDocument()
  })

  it('desfazer a resposta tira a pergunta e a loja volta a pedir revisão', async () => {
    const { user } = await renderTab()
    await user.click(within(row('iFood')).getByRole('button', { name: /Food › Restaurants/ }))
    await within(row('iFood')).findByText(ask)

    serverGroups = [ifood, ...serverGroups]
    const options = toast.success.mock.calls[0][1] as { action: { onClick: () => void } }
    await act(async () => options.action.onClick())

    expect(api.categorization.undo).toHaveBeenCalledWith({ event_ids: ['IFOOD-e1', 'IFOOD-e2'] })
    // The refetch brings the store back as a store to review.
    await waitFor(() =>
      expect(within(row('iFood')).getByRole('button', { name: /Food › Restaurants/ })).toBeInTheDocument(),
    )
    expect(screen.queryByText(ask)).not.toBeInTheDocument()
  })

  it('criar a categoria nova da própria linha também pergunta', async () => {
    serverGroups = [{ ...ifood, new_category: { group: 'Leisure', name: 'Delivery' } }, uber, maria]
    api.categorization.createCategory.mockImplementation(async () => {
      serverGroups = serverGroups.filter((g) => g.merchant_key !== 'IFOOD')
      return { category_id: 'c9', applied: 12, event_ids: ['n1'] }
    })
    const { user } = await renderTab()
    await user.click(
      within(row('iFood')).getByRole('button', {
        name: t('categorization.review.newCategory', { path: 'Leisure › Delivery' }),
      }),
    )
    expect(api.categorization.createCategory).toHaveBeenCalledWith({
      group: 'Leisure',
      name: 'Delivery',
      merchant_keys: ['IFOOD'],
    })
    expect(await within(row('iFood')).findByText(ask)).toBeInTheDocument()
    await user.click(within(row('iFood')).getByRole('button', { name: applyTwo }))
    expect(api.categorization.applySimilar).toHaveBeenCalledWith('t1', {
      excludeRule: true,
      transactionIds: ['a', 'b'],
    })
  })
})

describe('ReviewTab: parecidas da loja, bordas', () => {
  const ask = t('categorization.similar.ask', { count: 2 })

  beforeEach(() => {
    api.categorization.similar.mockImplementation(async (id: string) =>
      id === 't1' || id === 't2' ? twoSimilar : noSimilar,
    )
  })

  it('a linha respondida descreve-se pela pergunta, para o leitor de tela ouvir antes do Enter', async () => {
    const { user } = await renderTab()
    act(() => row('iFood').focus())
    await user.keyboard('1')
    await within(row('iFood')).findByText(ask)
    const describedBy = row('iFood').getAttribute('aria-describedby')
    expect(describedBy).toBeTruthy()
    expect(document.getElementById(describedBy!)).toHaveTextContent(ask)
    expect(document.getElementById(describedBy!)).toHaveAttribute('role', 'status')
  })

  it('a referência é uma linha aplicada: com os ids do servidor, a primeira delas', async () => {
    api.categorization.accept.mockResolvedValueOnce({
      applied: 1,
      event_ids: ['e1'],
      event_run_id: null,
      transaction_ids: ['t2'],
    })
    const { user } = await renderTab()
    await user.click(within(row('iFood')).getByRole('button', { name: /Food › Restaurants/ }))
    await within(row('iFood')).findByText(ask)
    expect(api.categorization.similar).toHaveBeenCalledWith('t2', { excludeRule: true })
  })

  it('sem saber quais foram aplicadas e com alguma pulada, não pergunta', async () => {
    // Two pending rows, one applied: the first may be the one skipped.
    api.categorization.accept.mockImplementationOnce(async () => {
      serverGroups = serverGroups.filter((g) => g.merchant_key !== 'IFOOD')
      return { applied: 1, event_ids: ['e1'], event_run_id: null }
    })
    const { user } = await renderTab()
    await user.click(within(row('iFood')).getByRole('button', { name: /Food › Restaurants/ }))
    await waitFor(() => expect(screen.queryByText('iFood')).not.toBeInTheDocument())
    expect(api.categorization.similar).not.toHaveBeenCalled()
  })

  it('falha no envio: a linha volta no mesmo lugar, com os botões', async () => {
    let fail!: (reason: unknown) => void
    api.categorization.accept.mockReturnValueOnce(new Promise((_, reject) => (fail = reject)))
    const { user } = await renderTab()
    await user.click(within(row('Uber')).getByRole('button', { name: /Transport › Rides/ }))
    expect(within(row('Uber')).getByText(t('categorization.review.applying'))).toBeInTheDocument()
    expect(row('Uber').dataset.index).toBe('1')

    await act(async () => fail({ response: { status: 500, data: { detail: 'down' } } }))
    expect(toast.error).toHaveBeenCalledWith('down')
    expect(row('Uber').dataset.index).toBe('1')
    expect(within(row('Uber')).getByRole('button', { name: /Transport › Rides/ })).toBeInTheDocument()
  })

  it('uma linha respondida acima que sai não tira o foco da linha atual', async () => {
    const { user } = await renderTab()
    await user.click(within(row('iFood')).getByRole('button', { name: /Food › Restaurants/ }))
    await within(row('iFood')).findByText(ask)
    act(() => row('iFood').focus())
    await user.keyboard('{ArrowDown}')
    expect(row('Uber')).toHaveFocus()

    // Undoing iFood's answer takes its row away (until the refetch).
    const options = toast.success.mock.calls[0][1] as { action: { onClick: () => void } }
    await act(async () => options.action.onClick())
    expect(row('Uber')).toHaveFocus()
    expect(row('Uber').tabIndex).toBe(0)
    expect(row('Maria Silva').tabIndex).toBe(-1)
  })

  it('Enter e Escape na busca do seletor de outra loja não respondem a pergunta', async () => {
    const { user } = await renderTab()
    await user.click(within(row('iFood')).getByRole('button', { name: /Food › Restaurants/ }))
    await within(row('iFood')).findByText(ask)

    await user.click(within(row('Uber')).getByRole('button', { name: t('categorization.review.other') }))
    const search = await screen.findByPlaceholderText(t('transactions.searchCategory'))
    await user.type(search, 'zzz{Enter}')
    await user.keyboard('{Escape}')

    expect(api.categorization.applySimilar).not.toHaveBeenCalled()
    expect(within(row('iFood')).getByText(ask)).toBeInTheDocument()
  })

  it('uma proposta de categoria nova para uma loja só também pergunta', async () => {
    proposals = [{ group: 'Leisure', name: 'Delivery', merchant_keys: ['IFOOD'], count: 12, total: '340.50' }]
    api.categorization.createCategory.mockImplementation(async () => {
      serverGroups = serverGroups.filter((g) => g.merchant_key !== 'IFOOD')
      proposals = []
      return { category_id: 'c9', applied: 12, event_ids: ['n1'] }
    })
    const { user } = await renderTab()
    await user.click(screen.getByRole('button', { name: t('categorization.review.createAndApply') }))
    expect(api.categorization.createCategory).toHaveBeenCalledWith({
      group: 'Leisure',
      name: 'Delivery',
      merchant_keys: ['IFOOD'],
    })
    expect(await within(row('iFood')).findByText(ask)).toBeInTheDocument()
    expect(
      screen.queryByText(t('categorization.review.proposal', { path: 'Leisure › Delivery' })),
    ).not.toBeInTheDocument()
  })
})

describe('ReviewTab: perguntar à IA', () => {
  const summary = (overrides: Record<string, unknown> = {}) => ({
    pending_groups: 3, pending_transactions: 19, applied_last_7d: 0, last_run: null,
    ai_available: true, ai_enabled: true, month_cost_usd: '0', monthly_cap_usd: '2', ...overrides,
  })
  const askButton = (name: string) =>
    within(row(name)).getByRole('button', { name: t('categorization.ask.button') })

  async function renderAsk() {
    const utils = renderWithProviders(
      <AskProvider>
        <ReviewTab />
      </AskProvider>,
    )
    await screen.findByText('iFood')
    return utils
  }

  beforeEach(() => {
    api.categorization.summary.mockResolvedValue(summary())
    api.categorization.ask.mockResolvedValue({
      reply: 'Mercado, então.',
      suggestion: { category_id: 'c2', new_category: null, group_exists: true, confidence: 90, applies_to: 2 },
      note: 'compras do mês',
      assistant_turn: '{"reply":"Mercado, então."}',
      person_messages_left: 29,
    })
    api.categorization.choose.mockImplementation(async () => {
      serverGroups = serverGroups.filter((g) => g.merchant_key !== 'IFOOD')
      return { category_id: 'c2', created_group: false, created_category: false, applied: 12, event_ids: ['ch1'] }
    })
    api.categorization.similar.mockImplementation(async (id: string) => (id === 't1' ? twoSimilar : noSimilar))
  })

  it('com a IA desligada o botão fica desabilitado com o motivo', async () => {
    api.categorization.summary.mockResolvedValue(summary({ ai_enabled: false }))
    await renderAsk()
    await waitFor(() => expect(askButton('iFood')).toBeDisabled())
    expect(askButton('iFood')).toHaveAttribute('title', t('categorization.ask.blocked.disabled'))
  })

  it('quem só lê vê o botão desabilitado com blocked.readOnly', async () => {
    workspace.canWrite = false
    await renderAsk()
    expect(askButton('iFood')).toBeDisabled()
    expect(askButton('iFood')).toHaveAttribute('title', t('categorization.ask.blocked.readOnly'))
  })

  it('aceitar no chat aplica pela loja, pergunta pelas parecidas e devolve o foco à linha', async () => {
    const { user } = await renderAsk()
    await waitFor(() => expect(askButton('iFood')).toBeEnabled())
    await user.click(askButton('iFood'))
    const dialog = await screen.findByRole('dialog', { name: t('categorization.ask.title', { name: 'iFood' }) })
    expect(dialog).toHaveTextContent(t('categorization.ask.context', { count: 12, total: 'R$340.50', date: '10/1/2026' }))

    await user.type(within(dialog).getByRole('textbox'), 'é mercado{Enter}')
    expect(api.categorization.ask).toHaveBeenCalledWith({
      merchant_key: 'IFOOD',
      messages: [{ role: 'user', content: 'é mercado' }],
    })
    const card = await within(dialog).findByTestId('ask-suggestion')
    expect(card).toHaveTextContent('Food › Groceries')
    await user.click(within(card).getByRole('button', { name: t('categorization.ask.accept') }))

    expect(api.categorization.choose).toHaveBeenCalledWith({
      merchant_key: 'IFOOD',
      category_id: 'c2',
      note: 'compras do mês',
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await within(row('iFood')).findByText(t('categorization.similar.ask', { count: 2 }))).toBeInTheDocument()
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        t('categorization.ask.accepted'),
        expect.objectContaining({ action: expect.objectContaining({ label: t('common.undo') }) }),
      ),
    )
    await waitFor(() => expect(row('iFood')).toHaveFocus())
  })

  async function acceptInChat(user: ReturnType<typeof renderWithProviders>['user'], name: string) {
    await waitFor(() => expect(askButton(name)).toBeEnabled())
    await user.click(askButton(name))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByRole('textbox'), 'é mercado{Enter}')
    const card = await within(dialog).findByTestId('ask-suggestion')
    await user.click(within(card).getByRole('button', { name: t('categorization.ask.accept') }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  }

  it('linhas puladas (sem pergunta): a loja sai e o foco vai à próxima linha, não ao body', async () => {
    api.categorization.choose.mockImplementation(async () => {
      serverGroups = serverGroups.filter((g) => g.merchant_key !== 'IFOOD')
      return { category_id: 'c2', created_group: false, created_category: false, applied: 1, event_ids: ['ch1'] }
    })
    const { user } = await renderAsk()
    await acceptInChat(user, 'iFood')
    await waitFor(() => expect(screen.queryByText('iFood')).not.toBeInTheDocument())
    await waitFor(() => expect(row('Uber')).toHaveFocus())
    expect(api.categorization.similar).not.toHaveBeenCalled()
  })

  it('nada parecido: a loja sai e o foco vai à próxima linha', async () => {
    api.categorization.similar.mockResolvedValue(noSimilar)
    const { user } = await renderAsk()
    await acceptInChat(user, 'iFood')
    await waitFor(() => expect(screen.queryByText('iFood')).not.toBeInTheDocument())
    await waitFor(() => expect(row('Uber')).toHaveFocus())
    expect(document.activeElement).not.toBe(document.body)
  })

  it('a última linha que sai passa o foco à anterior', async () => {
    api.categorization.choose.mockImplementation(async () => {
      serverGroups = serverGroups.filter((g) => g.merchant_key !== 'PIX:MARIA SILVA')
      return { category_id: 'c2', created_group: false, created_category: false, applied: 0, event_ids: [] }
    })
    const { user } = await renderAsk()
    await acceptInChat(user, 'Maria Silva')
    await waitFor(() => expect(screen.queryByText('Maria Silva')).not.toBeInTheDocument())
    await waitFor(() => expect(row('Uber')).toHaveFocus())
  })

  it('sem nenhuma linha restante, o foco fica na aba (não no body)', async () => {
    serverGroups = [ifood]
    api.categorization.similar.mockResolvedValue(noSimilar)
    const { user } = await renderAsk()
    await acceptInChat(user, 'iFood')
    expect(await screen.findByText(t('categorization.review.empty'))).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('categorization-review')).toHaveFocus())
  })

  it('Pix sem nome: o título diz só "Pix"', async () => {
    serverGroups = [{ ...maria, merchant_key: 'PIX:#abc123', clean_name: null }, ifood]
    const { user } = await renderAsk()
    const first = document.querySelector('[data-review-row]') as HTMLElement
    const button = within(first).getByRole('button', { name: t('categorization.ask.button') })
    await waitFor(() => expect(button).toBeEnabled())
    await user.click(button)
    expect(
      await screen.findByRole('dialog', {
        name: t('categorization.ask.title', { name: t('categorization.review.pix') }),
      }),
    ).toBeInTheDocument()
  })

  it('atalho i abre a conversa da linha focada, e a legenda mostra o atalho', async () => {
    const { user } = await renderAsk()
    await waitFor(() => expect(askButton('Uber')).toBeEnabled())
    expect(screen.getByTestId('review-shortcuts')).toHaveTextContent(t('categorization.review.keys.ask'))
    act(() => row('iFood').focus())
    await user.keyboard('{ArrowDown}')
    expect(row('Uber')).toHaveAttribute('aria-keyshortcuts', expect.stringContaining('i'))
    await user.keyboard('i')
    expect(
      await screen.findByRole('dialog', { name: t('categorization.ask.title', { name: 'Uber' }) }),
    ).toBeInTheDocument()
  })

  it('atalho i não abre nada quando a conversa está bloqueada', async () => {
    api.categorization.summary.mockResolvedValue(summary({ ai_enabled: false }))
    const { user } = await renderAsk()
    await waitFor(() => expect(askButton('iFood')).toBeDisabled())
    expect(screen.getByTestId('review-shortcuts')).not.toHaveTextContent(t('categorization.review.keys.ask'))
    act(() => row('iFood').focus())
    await user.keyboard('i')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('"Perguntar à IA" no rodapé do "Outra..." abre o diálogo da loja', async () => {
    const { user } = await renderAsk()
    await waitFor(() => expect(askButton('Uber')).toBeEnabled())
    await user.click(within(row('Uber')).getByRole('button', { name: t('categorization.review.other') }))
    await user.click(await screen.findByRole('option', { name: t('categorization.ask.button') }))
    expect(
      await screen.findByRole('dialog', { name: t('categorization.ask.title', { name: 'Uber' }) }),
    ).toBeInTheDocument()
  })
})
