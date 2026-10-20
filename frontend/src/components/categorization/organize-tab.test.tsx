import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, screen, waitFor, within } from '@testing-library/react'

import { OrganizeTab } from '@/components/categorization/organize-tab'
import { organizerPollInterval } from '@/lib/categorization-organizer'
import { renderWithProviders, t } from '@/test/utils'
import type { CategorizationRun, OrganizerOp, OrganizerView } from '@/types'

const api = vi.hoisted(() => ({
  categorization: {
    organizer: vi.fn(),
    runOrganizer: vi.fn(),
    applyOrganizerOp: vi.fn(),
    rejectOrganizerOp: vi.fn(),
    retryOrganizerOp: vi.fn(),
  },
  categories: { list: vi.fn() },
  categoryGroups: { list: vi.fn() },
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
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
const lazer = cat('lazer', 'Lazer', 'g-lazer')
const compras = cat('compras', 'Compras', 'g-casa')
const online = cat('online', 'Compras Online', 'g-casa')
const mercado = cat('mercado', 'Mercado', 'g-casa')
const groupsFixture = [
  { id: 'g-lazer', name: 'Lazer', icon: 'x', color: '#000', user_id: 'u', position: 0, is_system: false, is_hidden: false, categories: [lazer] },
  { id: 'g-casa', name: 'Casa', icon: 'x', color: '#000', user_id: 'u', position: 1, is_system: false, is_hidden: false, categories: [compras, online, mercado] },
]

function run(status: string, overrides: Partial<CategorizationRun> = {}): CategorizationRun {
  return {
    id: 'run-1',
    kind: 'organize',
    trigger: 'manual',
    status,
    stats: {},
    cost_usd: '0.21',
    error: null,
    created_at: new Date().toISOString(),
    finished_at: null,
    ...overrides,
  }
}

function op(overrides: Partial<OrganizerOp> & Pick<OrganizerOp, 'id' | 'op'>): OrganizerOp {
  return {
    target: null,
    group: null,
    name: null,
    children: [],
    into: null,
    why: '',
    impact: { tx: 0, total: 0 },
    labels: {},
    status: 'pending',
    decided_at: null,
    result: null,
    redistribution: null,
    ...overrides,
  }
}

const games = op({
  id: 'o1',
  op: 'create_category',
  group: 'g1',
  name: 'Games',
  children: ['i1', 'i2', 'i3'],
  why: 'Jogos aparecem todo mês',
  impact: { tx: 14, total: 820 },
  labels: { group: 'Lazer', children: ['Steam', 'PSN', 'Nuuvem'] },
})
const merge = op({
  id: 'o2',
  op: 'merge',
  target: 'c3',
  into: 'c2',
  why: 'Repetidas',
  impact: { tx: 5, total: 300 },
  labels: { target: 'Compras Online', into: 'Compras' },
})
const rename = op({
  id: 'o3',
  op: 'rename',
  target: 'c4',
  name: 'Supermercado',
  impact: { tx: 40, total: 5000 },
  labels: { target: 'Mercado' },
})
const split = op({
  id: 'o4',
  op: 'split_category',
  target: 'c1',
  name: 'Lazer',
  children: ['Assinaturas', 'Ingressos', 'Cinema'],
  impact: { tx: 30, total: 900 },
  labels: { target: 'Lazer', children: ['Assinaturas', 'Ingressos', 'Cinema'] },
})

let view: OrganizerView

beforeEach(() => {
  vi.clearAllMocks()
  workspace.canWrite = true
  workspace.canManage = true
  view = { run: run('done'), ops: [games, merge, rename], message: null }
  api.categorization.organizer.mockImplementation(async () => view)
  api.categories.list.mockResolvedValue([lazer, compras, online, mercado])
  api.categoryGroups.list.mockResolvedValue(groupsFixture)
  api.categorization.applyOrganizerOp.mockImplementation(async (id: string) => {
    const found = view.ops.find((o) => o.id === id)!
    const applied = { ...found, status: 'applied' as const }
    view = { ...view, ops: view.ops.map((o) => (o.id === id ? applied : o)) }
    return { op: applied, redistribute_run: null }
  })
  api.categorization.rejectOrganizerOp.mockImplementation(async (id: string) => {
    const found = view.ops.find((o) => o.id === id)!
    const rejected = { ...found, status: 'rejected' as const }
    view = { ...view, ops: view.ops.map((o) => (o.id === id ? rejected : o)) }
    return rejected
  })
  api.categorization.runOrganizer.mockImplementation(async () => {
    view = { run: run('queued', { id: 'run-2' }), ops: [], message: null }
    return view.run
  })
})

const card = (id: string) => screen.getByTestId(`organizer-op-${id}`)

function conflict(detail: string) {
  return Object.assign(new Error('409'), { isAxiosError: true, response: { status: 409, data: { detail } } })
}

async function renderProposal() {
  const utils = renderWithProviders(<OrganizeTab />)
  await screen.findByTestId('organizer-op-o1')
  return utils
}

describe('OrganizeTab', () => {
  it('sem proposta: explica, mostra custo e analisa', async () => {
    view = { run: null, ops: [], message: null }
    const { user } = renderWithProviders(<OrganizeTab />)
    const button = await screen.findByRole('button', { name: t('categorization.organize.analyze') })
    expect(screen.getByText(t('categorization.organize.costNote'))).toBeInTheDocument()
    expect(screen.getByText(t('categorization.organize.emptyHint'))).toBeInTheDocument()

    await user.click(button)
    expect(api.categorization.runOrganizer).toHaveBeenCalledTimes(1)
    expect(await screen.findByText(t('categorization.organize.running'))).toBeInTheDocument()
  })

  it('enquanto analisa, consulta de novo a cada poucos segundos', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      view = { run: run('running'), ops: [], message: null }
      renderWithProviders(<OrganizeTab />)
      expect(await screen.findByText(t('categorization.organize.running'))).toBeInTheDocument()
      expect(api.categorization.organizer).toHaveBeenCalledTimes(1)

      view = { run: run('done'), ops: [games], message: null }
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4100)
      })
      expect(api.categorization.organizer).toHaveBeenCalledTimes(2)
      expect(await screen.findByTestId('organizer-op-o1')).toBeInTheDocument()
      const calls = api.categorization.organizer.mock.calls.length
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10000)
      })
      expect(api.categorization.organizer).toHaveBeenCalledTimes(calls)
    } finally {
      vi.useRealTimers()
    }
  })

  it('poll: só com run ocupado ou redistribuição em andamento', () => {
    expect(organizerPollInterval(undefined)).toBe(false)
    expect(organizerPollInterval({ run: run('queued'), ops: [], message: null })).toBeGreaterThan(0)
    expect(organizerPollInterval({ run: run('done'), ops: [games], message: null })).toBe(false)
    expect(organizerPollInterval({
      run: run('done'),
      ops: [{ ...split, status: 'applied', redistribution: run('processing_batch', { kind: 'redistribute' }) }],
      message: null,
    })).toBeGreaterThan(0)
  })

  it('falha: mostra a mensagem e tenta de novo', async () => {
    view = { run: run('capped'), ops: [], message: 'O teto mensal de gastos com IA foi atingido.' }
    const { user } = renderWithProviders(<OrganizeTab />)
    expect(await screen.findByText('O teto mensal de gastos com IA foi atingido.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('categorization.organize.retry') }))
    expect(api.categorization.runOrganizer).toHaveBeenCalled()
  })

  it('lista as operações com título, motivo e impacto', async () => {
    await renderProposal()
    expect(within(card('o1')).getByText(t('categorization.organize.title.create_category', { path: 'Lazer › Games' }))).toBeInTheDocument()
    expect(within(card('o1')).getByText('Jogos aparecem todo mês')).toBeInTheDocument()
    expect(within(card('o1')).getByText(/14 transactions · R\$820\.00 in 12 months/)).toBeInTheDocument()
    expect(within(card('o2')).getByText(t('categorization.organize.title.merge', { from: 'Compras Online', into: 'Compras' }))).toBeInTheDocument()
    expect(within(card('o3')).getByText(t('categorization.organize.title.rename', { from: 'Mercado', to: 'Supermercado' }))).toBeInTheDocument()
  })

  it('criar categoria diz quais lojas e quantas transações vão mudar (R24)', async () => {
    await renderProposal()
    expect(within(card('o1')).getByText(/Moves 14 transactions from 3 stores to Lazer › Games, including the ones you categorized yourself/)).toBeInTheDocument()
    expect(within(card('o1')).getByText('Stores: Steam, PSN, Nuuvem')).toBeInTheDocument()
  })

  it('dividir avisa da redistribuição pela IA', async () => {
    view = { ...view, ops: [games, split] }
    await renderProposal()
    expect(within(card('o4')).getByText(t('categorization.organize.title.split_category', { name: 'Lazer', count: 3 }))).toBeInTheDocument()
    expect(within(card('o4')).getByText(/AI redistributes 30 transactions from Lazer among the new categories/)).toBeInTheDocument()
    expect(within(card('o4')).getByText(/Assinaturas keeps the history, budgets and rules of Lazer/)).toBeInTheDocument()
  })

  it('aplicar chama a rota, o card fica verde e invalida categorias', async () => {
    const { user, queryClient } = await renderProposal()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    expect(card('o1')).toHaveAttribute('data-status', 'pending')

    await user.click(within(card('o1')).getByRole('button', { name: t('categorization.organize.apply') }))
    expect(api.categorization.applyOrganizerOp).toHaveBeenCalledWith('o1', 'run-1')
    await waitFor(() => expect(card('o1')).toHaveAttribute('data-status', 'applied'))
    expect(within(card('o1')).getByText(t('categorization.organize.status.applied'))).toBeInTheDocument()
    expect(within(card('o1')).queryByRole('button', { name: t('categorization.organize.apply') })).toBeNull()
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['categories'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['categorization'] })
  })

  it('recusar deixa o card cinza', async () => {
    const { user } = await renderProposal()
    await user.click(within(card('o3')).getByRole('button', { name: t('categorization.organize.reject') }))
    expect(api.categorization.rejectOrganizerOp).toHaveBeenCalledWith('o3', 'run-1')
    await waitFor(() => expect(card('o3')).toHaveAttribute('data-status', 'rejected'))
  })

  it('mesclar exige confirmação', async () => {
    const { user } = await renderProposal()
    await user.click(within(card('o2')).getByRole('button', { name: t('categorization.organize.apply') }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(t('categorization.organize.mergeTitle'))).toBeInTheDocument()
    expect(api.categorization.applyOrganizerOp).not.toHaveBeenCalled()

    await user.click(within(dialog).getByRole('button', { name: t('categorization.organize.mergeConfirm') }))
    expect(api.categorization.applyOrganizerOp).toHaveBeenCalledWith('o2', 'run-1')
    await waitFor(() => expect(card('o2')).toHaveAttribute('data-status', 'applied'))
  })

  it('conflito 409: mostra o motivo e oferece nova proposta', async () => {
    api.categorization.applyOrganizerOp.mockRejectedValueOnce(
      conflict('As categorias mudaram desde a proposta — gere outra.'),
    )
    const { user } = await renderProposal()
    await user.click(within(card('o3')).getByRole('button', { name: t('categorization.organize.apply') }))
    expect(await within(card('o3')).findByText('As categorias mudaram desde a proposta — gere outra.')).toBeInTheDocument()
    expect(card('o3')).toHaveAttribute('data-status', 'pending')

    await user.click(within(card('o3')).getByRole('button', { name: t('categorization.organize.newProposal') }))
    // Pending ops would be discarded: confirm first.
    const dialog = await screen.findByRole('dialog')
    expect(api.categorization.runOrganizer).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole('button', { name: t('categorization.organize.newProposal') }))
    expect(api.categorization.runOrganizer).toHaveBeenCalled()
  })

  it('o erro de conflito some quando chega outra proposta', async () => {
    api.categorization.applyOrganizerOp.mockRejectedValueOnce(conflict('Já existe uma categoria.'))
    const { user, queryClient } = await renderProposal()
    await user.click(within(card('o3')).getByRole('button', { name: t('categorization.organize.apply') }))
    expect(await within(card('o3')).findByText('Já existe uma categoria.')).toBeInTheDocument()

    view = { run: run('done', { id: 'run-2' }), ops: [games, merge, rename], message: null }
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['categorization', 'organizer'] })
    })
    await waitFor(() => expect(within(card('o3')).queryByText('Já existe uma categoria.')).toBeNull())
  })

  it('nova proposta com mudanças pendentes pede confirmação com o custo', async () => {
    const { user } = await renderProposal()
    await user.click(screen.getByRole('button', { name: t('categorization.organize.newProposal') }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(t('categorization.organize.newProposalDescription', { count: 3 }))).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: t('common.cancel') }))
    expect(api.categorization.runOrganizer).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: t('categorization.organize.newProposal') }))
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: t('categorization.organize.newProposal') }))
    expect(api.categorization.runOrganizer).toHaveBeenCalledTimes(1)
    expect(await screen.findByText(t('categorization.organize.running'))).toBeInTheDocument()
  })

  it('nova proposta sem pendências vai direto e mostra o custo no título', async () => {
    view = { ...view, ops: view.ops.map((o) => ({ ...o, status: 'rejected' as const })) }
    const { user } = await renderProposal()
    const button = screen.getByRole('button', { name: t('categorization.organize.newProposal') })
    expect(button).toHaveAttribute('title', t('categorization.organize.costNote'))
    await user.click(button)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(api.categorization.runOrganizer).toHaveBeenCalledTimes(1)
  })

  it('erro ao carregar sem dados: cartão com tentar de novo', async () => {
    api.categorization.organizer.mockRejectedValueOnce(new Error('network'))
    const { user } = renderWithProviders(<OrganizeTab />)
    expect(await screen.findByText(t('categorization.organize.loadError'))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('categorization.organize.retry') }))
    expect(await screen.findByTestId('organizer-op-o1')).toBeInTheDocument()
    expect(api.categorization.organizer).toHaveBeenCalledTimes(2)
  })

  it('falha ao atualizar com dados: mantém a proposta e avisa discretamente', async () => {
    const { queryClient } = await renderProposal()
    api.categorization.organizer.mockRejectedValueOnce(new Error('network'))
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ['categorization', 'organizer'] })
    })
    expect(await screen.findByText(t('categorization.organize.refreshError'))).toBeInTheDocument()
    expect(card('o1')).toBeInTheDocument()
    expect(screen.queryByText(t('categorization.organize.loadError'))).toBeNull()
  })

  it('a árvore continua simulando a op aplicada até as categorias recarregarem', async () => {
    const { user } = await renderProposal()
    // The category refetch never answers.
    api.categories.list.mockImplementation(() => new Promise(() => {}))
    await user.click(within(card('o3')).getByRole('button', { name: t('categorization.organize.apply') }))
    await waitFor(() => expect(card('o3')).toHaveAttribute('data-status', 'applied'))
    const tree = screen.getByTestId('organizer-tree')
    expect(within(tree).getByText('Supermercado')).toBeInTheDocument()
    expect(within(tree).getByText(t('categorization.organize.before', { name: 'Mercado' }))).toBeInTheDocument()
  })

  it('criar grupo avisa quando outras mudanças usam o grupo', async () => {
    view = {
      ...view,
      ops: [
        op({ id: 'o5', op: 'create_group', name: 'Bichos' }),
        op({ id: 'o6', op: 'move_category', target: 'c4', group: 'Bichos', labels: { target: 'Mercado', group: 'Bichos' } }),
        { ...games, id: 'o1' },
      ],
    }
    await renderProposal()
    expect(within(card('o5')).getByText(t('categorization.organize.effect.groupUsedLater'))).toBeInTheDocument()
  })

  it('aplicar todas: confirma as mesclagens uma vez e aplica em ordem', async () => {
    const { user } = await renderProposal()
    await user.click(screen.getByRole('button', { name: t('categorization.organize.applyAll', { count: 3 }) }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Compras Online/)).toBeInTheDocument()
    expect(api.categorization.applyOrganizerOp).not.toHaveBeenCalled()

    await user.click(within(dialog).getByRole('button', { name: t('categorization.organize.applyAllConfirm') }))
    await waitFor(() => expect(api.categorization.applyOrganizerOp).toHaveBeenCalledTimes(3))
    expect(api.categorization.applyOrganizerOp.mock.calls.map((c) => c[0])).toEqual(['o1', 'o2', 'o3'])
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(t('categorization.organize.allApplied', { count: 3 })))
  })

  it('aplicar todas para no primeiro conflito e avisa', async () => {
    view = { ...view, ops: [games, rename, op({ id: 'o5', op: 'create_group', name: 'Bichos' })] }
    api.categorization.applyOrganizerOp
      .mockImplementationOnce(async () => ({ op: { ...games, status: 'applied' }, redistribute_run: null }))
      .mockRejectedValueOnce(conflict('Já existe uma categoria "Supermercado" em "Casa".'))
    const { user } = await renderProposal()
    // No merge in the batch: no confirmation.
    await user.click(screen.getByRole('button', { name: t('categorization.organize.applyAll', { count: 3 }) }))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(api.categorization.applyOrganizerOp.mock.calls.map((c) => c[0])).toEqual(['o1', 'o3'])
    expect(String(toast.error.mock.calls[0][0])).toContain('Já existe uma categoria')
    expect(String(toast.error.mock.calls[0][0])).toContain('1 of 3')
    expect(within(card('o3')).getByText('Já existe uma categoria "Supermercado" em "Casa".')).toBeInTheDocument()
    expect(card('o5')).toHaveAttribute('data-status', 'pending')
  })

  it('divisão aplicada acompanha a redistribuição e permite tentar de novo', async () => {
    view = { ...view, ops: [games, split] }
    api.categorization.applyOrganizerOp.mockImplementationOnce(async () => {
      const applied = { ...split, status: 'applied' as const, redistribution: run('queued', { id: 'r1', kind: 'redistribute' }) }
      view = { ...view, ops: [games, applied] }
      return { op: { ...split, status: 'applied' }, redistribute_run: run('queued', { id: 'r1', kind: 'redistribute' }) }
    })
    const { user } = await renderProposal()
    await user.click(within(card('o4')).getByRole('button', { name: t('categorization.organize.apply') }))
    expect(await within(card('o4')).findByText(t('categorization.organize.redistributing'))).toBeInTheDocument()

    // Failed later: the card offers a retry.
    view = {
      ...view,
      ops: [games, { ...split, status: 'applied', redistribution: run('failed', { id: 'r1', kind: 'redistribute', error: 'invalid_answer' }) }],
    }
    api.categorization.retryOrganizerOp.mockImplementation(async () => {
      const queued = run('queued', { id: 'r2', kind: 'redistribute' })
      view = { ...view, ops: [games, { ...split, status: 'applied', redistribution: queued }] }
      return { op: { ...split, status: 'applied' }, redistribute_run: queued }
    })
    const retry = await within(card('o4')).findByRole('button', { name: t('categorization.organize.retry') }, { timeout: 6000 })
    expect(within(card('o4')).getByText(t('categorization.organize.redistributeFailed', { name: 'Assinaturas' }))).toBeInTheDocument()
    await user.click(retry)
    expect(api.categorization.retryOrganizerOp).toHaveBeenCalledWith('o4', 'run-1')
    expect(await within(card('o4')).findByText(t('categorization.organize.redistributing'))).toBeInTheDocument()
  }, 10000)

  it('aplicar todas que para na primeira op ainda recarrega categorias e proposta', async () => {
    api.categorization.applyOrganizerOp.mockRejectedValueOnce(conflict('As categorias mudaram desde a proposta — gere outra.'))
    view = { ...view, ops: [games, rename] }
    const { user, queryClient } = await renderProposal()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    await user.click(screen.getByRole('button', { name: t('categorization.organize.applyAll', { count: 2 }) }))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['categories'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['categorization'] })
  })

  it('redistribuição sem conexão Claude aponta para as configurações', async () => {
    view = {
      ...view,
      ops: [games, { ...split, status: 'applied', redistribution: run('failed', { id: 'r1', kind: 'redistribute', error: 'no_ai_connection' }) }],
    }
    await renderProposal()
    const link = within(card('o4')).getByRole('link', { name: t('categorization.organize.configure') })
    expect(link).toHaveAttribute('href', '/workspace/settings#categorization')
    expect(within(card('o4')).queryByRole('button', { name: t('categorization.organize.retry') })).toBeNull()
  })

  it('a árvore prevista marca novas, renomeadas e mescladas', async () => {
    await renderProposal()
    const tree = screen.getByTestId('organizer-tree')
    const games = within(tree).getByText('Games').closest('[data-tree-category]') as HTMLElement
    expect(within(games).getByText(t('categorization.organize.newBadge'))).toBeInTheDocument()
    expect(within(tree).getByText(t('categorization.organize.before', { name: 'Mercado' }))).toBeInTheDocument()
    expect(within(tree).getByText('Compras Online')).toHaveClass('line-through')
  })

  it('grupo tocado continua aberto depois que suas operações saem da fila', async () => {
    const { user } = await renderProposal()
    const tree = screen.getByTestId('organizer-tree')
    await user.click(within(card('o2')).getByRole('button', { name: t('categorization.organize.reject') }))
    await waitFor(() => expect(card('o2')).toHaveAttribute('data-status', 'rejected'))
    await user.click(within(card('o3')).getByRole('button', { name: t('categorization.organize.reject') }))
    await waitFor(() => expect(card('o3')).toHaveAttribute('data-status', 'rejected'))
    expect(within(tree).getByText('Compras Online')).not.toHaveClass('line-through')
    expect(within(tree).getByText('Mercado')).toBeInTheDocument()
  })

  it('sem permissão de gestor: proposta só para leitura', async () => {
    workspace.canManage = false
    await renderProposal()
    expect(screen.queryByRole('button', { name: t('categorization.organize.apply') })).toBeNull()
    expect(screen.queryByRole('button', { name: t('categorization.organize.reject') })).toBeNull()
    expect(screen.queryByRole('button', { name: t('categorization.organize.applyAll', { count: 3 }) })).toBeNull()
    expect(screen.queryByRole('button', { name: t('categorization.organize.newProposal') })).toBeNull()
    expect(screen.getByText(t('categorization.organize.managersOnly'))).toBeInTheDocument()
  })

  it('sem permissão de gestor e sem proposta: sem botão de analisar', async () => {
    workspace.canManage = false
    view = { run: null, ops: [], message: null }
    renderWithProviders(<OrganizeTab />)
    expect(await screen.findByText(t('categorization.organize.managersOnly'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('categorization.organize.analyze') })).toBeNull()
  })
})
