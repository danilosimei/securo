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
