import { act, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SimilarPrompt } from '@/components/categorization/similar-prompt'
import { renderWithProviders, t } from '@/test/utils'
import type { SimilarResult, SimilarTransaction } from '@/types'

const api = vi.hoisted(() => ({
  categorization: { similar: vi.fn(), applySimilar: vi.fn(), undo: vi.fn() },
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
const workspace = vi.hoisted(() => ({ canWrite: true }))

vi.mock('@/lib/api', () => api)
vi.mock('sonner', () => ({ toast }))
vi.mock('@/contexts/workspace-context', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/workspace-context')>()),
  useWorkspace: () => workspace,
}))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

const tx = (id: string, description: string): SimilarTransaction => ({
  id,
  date: '2026-10-01',
  description,
  amount: '25.50',
  currency: 'BRL',
  type: 'debit',
  category_id: 'c2',
  category_source: 'ai',
})

const two: SimilarResult = {
  merchant_key: 'IFOOD',
  count: 2,
  transaction_ids: ['a', 'b'],
  transactions: [tx('a', 'IFOOD *PEDIDO A'), tx('b', 'IFOOD *PEDIDO B')],
}

beforeEach(() => {
  vi.clearAllMocks()
  workspace.canWrite = true
  api.categorization.similar.mockResolvedValue(two)
  api.categorization.applySimilar.mockResolvedValue({ applied: 2, event_ids: ['e1', 'e2'], event_run_id: null })
  api.categorization.undo.mockResolvedValue({ restored: 2, skipped: 0 })
})

function renderPrompt(onDone = vi.fn()) {
  const utils = renderWithProviders(
    <SimilarPrompt referenceId="ref" storeName="iFood" currency="BRL" onDone={onDone} />,
  )
  return { ...utils, onDone }
}

describe('SimilarPrompt', () => {
  it('pergunta pelas parecidas da loja deixando as de regra de fora', async () => {
    renderPrompt()
    expect(await screen.findByText(t('categorization.similar.ask', { count: 2 }))).toBeInTheDocument()
    expect(api.categorization.similar).toHaveBeenCalledWith('ref', { excludeRule: true })
    expect(screen.getByRole('button', { name: t('categorization.similar.apply', { count: 2 }) })).toBeInTheDocument()
  })

  it('"Ver quais" lista as parecidas e esconde de novo', async () => {
    const { user } = renderPrompt()
    await user.click(await screen.findByRole('button', { name: t('categorization.similar.show') }))
    expect(screen.getByText('IFOOD *PEDIDO A')).toBeInTheDocument()
    expect(screen.getByText('IFOOD *PEDIDO B')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('categorization.similar.hide') }))
    expect(screen.queryByText('IFOOD *PEDIDO A')).not.toBeInTheDocument()
  })

  it('"Aplicar às 2" aplica exatamente as mostradas e o toast desfaz', async () => {
    const { user, onDone } = renderPrompt()
    await user.click(await screen.findByRole('button', { name: t('categorization.similar.apply', { count: 2 }) }))

    expect(api.categorization.applySimilar).toHaveBeenCalledWith('ref', {
      excludeRule: true,
      transactionIds: ['a', 'b'],
    })
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
    expect(toast.success).toHaveBeenCalledWith(
      t('categorization.similar.applied', { count: 2 }),
      expect.objectContaining({ action: expect.objectContaining({ label: t('common.undo') }) }),
    )

    const options = toast.success.mock.calls[0][1] as { action: { onClick: () => void } }
    await act(async () => options.action.onClick())
    expect(api.categorization.undo).toHaveBeenCalledWith({ event_ids: ['e1', 'e2'] })
  })

  it('erro ao aplicar mostra o erro e mantém a pergunta', async () => {
    api.categorization.applySimilar.mockRejectedValueOnce({
      response: { status: 422, data: { detail: 'boom' } },
    })
    const { user, onDone } = renderPrompt()
    await user.click(await screen.findByRole('button', { name: t('categorization.similar.apply', { count: 2 }) }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('boom'))
    expect(onDone).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: t('categorization.similar.apply', { count: 2 }) })).toBeEnabled()
  })

  it('"Não" encerra sem aplicar', async () => {
    const { user, onDone } = renderPrompt()
    await user.click(await screen.findByRole('button', { name: t('categorization.similar.no') }))
    expect(onDone).toHaveBeenCalledTimes(1)
    expect(api.categorization.applySimilar).not.toHaveBeenCalled()
  })

  it('Escape dentro da pergunta é o "Não"', async () => {
    const { user, onDone } = renderPrompt()
    await user.click(await screen.findByRole('button', { name: t('categorization.similar.show') }))
    await user.keyboard('{Escape}')
    expect(onDone).toHaveBeenCalledTimes(1)
  })

  it('sem parecidas chama onDone e não mostra nada', async () => {
    api.categorization.similar.mockResolvedValue({ merchant_key: 'IFOOD', count: 0, transaction_ids: [], transactions: [] })
    const { container, onDone } = renderPrompt()
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
    expect(container).toBeEmptyDOMElement()
  })

  it('erro ao buscar as parecidas encerra em silêncio', async () => {
    api.categorization.similar.mockRejectedValue(new Error('offline'))
    const { onDone } = renderPrompt()
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('quem só lê não vê botões nem consulta', async () => {
    workspace.canWrite = false
    const { onDone } = renderPrompt()
    await waitFor(() => expect(onDone).toHaveBeenCalled())
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(api.categorization.similar).not.toHaveBeenCalled()
  })

  it('com o nome da loja à mostra na barra', async () => {
    renderWithProviders(
      <SimilarPrompt referenceId="ref" storeName="iFood" currency="BRL" onDone={vi.fn()} showStore />,
    )
    expect(await screen.findByText(t('categorization.similar.store', { store: 'iFood' }))).toBeInTheDocument()
  })
})

describe('SimilarPrompt: leitor de tela, limites e blocos', () => {
  it('a pergunta entra numa região viva que já existia vazia', async () => {
    let resolve!: (value: SimilarResult) => void
    api.categorization.similar.mockReturnValue(new Promise<SimilarResult>((r) => (resolve = r)))
    renderWithProviders(
      <SimilarPrompt referenceId="ref" storeName="iFood" currency="BRL" onDone={vi.fn()} questionId="q1" />,
    )
    const live = screen.getByRole('status')
    expect(live).toHaveAttribute('aria-live', 'polite')
    expect(live).toHaveAttribute('id', 'q1')
    expect(live).toBeEmptyDOMElement()

    await act(async () => resolve(two))
    // The same node, now with the question: the change is what is announced.
    await waitFor(() => expect(live).toHaveTextContent(t('categorization.similar.ask', { count: 2 })))
    expect(screen.getByRole('status')).toBe(live)
    expect(screen.getByRole('group', { name: t('categorization.similar.ask', { count: 2 }) })).toBeInTheDocument()
  })

  it('"Ver quais" mostra as primeiras 50 e conta o resto', async () => {
    const many = Array.from({ length: 60 }, (_, i) => tx(`r${i}`, `IFOOD *ROW ${i}`))
    api.categorization.similar.mockResolvedValue({
      merchant_key: 'IFOOD',
      count: 60,
      transaction_ids: many.map((m) => m.id),
      transactions: many,
    })
    const { user } = renderPrompt()
    await user.click(await screen.findByRole('button', { name: t('categorization.similar.show') }))
    expect(screen.getByText('IFOOD *ROW 49')).toBeInTheDocument()
    expect(screen.queryByText('IFOOD *ROW 50')).not.toBeInTheDocument()
    expect(screen.getByText(t('categorization.review.more', { count: 10 }))).toBeInTheDocument()
  })

  it('mais de 5000 parecidas vão em blocos e um só toast desfaz todas', async () => {
    const ids = Array.from({ length: 5001 }, (_, i) => `x${i}`)
    api.categorization.similar.mockResolvedValue({
      merchant_key: 'IFOOD',
      count: ids.length,
      transaction_ids: ids,
      transactions: ids.map((id) => tx(id, id)),
    })
    api.categorization.applySimilar
      .mockResolvedValueOnce({ applied: 5000, event_ids: ['b1'], event_run_id: null })
      .mockResolvedValueOnce({ applied: 1, event_ids: ['b2'], event_run_id: null })
    const { user, onDone } = renderPrompt()
    await user.click(
      await screen.findByRole('button', { name: t('categorization.similar.apply', { count: 5001 }) }),
    )
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
    expect(api.categorization.applySimilar).toHaveBeenNthCalledWith(1, 'ref', {
      excludeRule: true,
      transactionIds: ids.slice(0, 5000),
    })
    expect(api.categorization.applySimilar).toHaveBeenNthCalledWith(2, 'ref', {
      excludeRule: true,
      transactionIds: ['x5000'],
    })
    expect(toast.success).toHaveBeenCalledWith(
      t('categorization.similar.applied', { count: 5001 }),
      expect.anything(),
    )
    const options = toast.success.mock.calls[0][1] as { action: { onClick: () => void } }
    await act(async () => options.action.onClick())
    expect(api.categorization.undo).toHaveBeenCalledWith({ event_ids: ['b1', 'b2'] })
  })

  it('o desfazer vai em lotes de até 5000 eventos', async () => {
    const events = Array.from({ length: 5001 }, (_, i) => `e${i}`)
    api.categorization.applySimilar.mockResolvedValue({ applied: 2, event_ids: events, event_run_id: null })
    const { user } = renderPrompt()
    await user.click(await screen.findByRole('button', { name: t('categorization.similar.apply', { count: 2 }) }))
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    const options = toast.success.mock.calls[0][1] as { action: { onClick: () => void } }
    await act(async () => options.action.onClick())
    expect(api.categorization.undo).toHaveBeenCalledTimes(2)
    expect(api.categorization.undo).toHaveBeenLastCalledWith({ event_ids: ['e5000'] })
  })
})
