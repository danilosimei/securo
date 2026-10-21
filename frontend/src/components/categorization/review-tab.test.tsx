import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, screen, waitFor, within } from '@testing-library/react'

import { WorkspaceContext } from '@/contexts/workspace-context'
import { ReviewTab } from '@/components/categorization/review-tab'
import { renderWithProviders, t } from '@/test/utils'
import type { NewCategoryProposal, ReviewGroup, ReviewTransaction } from '@/types'

const api = vi.hoisted(() => ({
  categorization: {
    review: vi.fn(),
    accept: vi.fn(),
    dismiss: vi.fn(),
    createCategory: vi.fn(),
    undo: vi.fn(),
    setNote: vi.fn(),
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
