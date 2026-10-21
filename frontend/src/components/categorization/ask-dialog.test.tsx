import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { AxiosError, AxiosHeaders } from 'axios'

import { AskProvider, useAsk, type AskTarget } from '@/components/categorization/ask-provider'
import { renderWithProviders, t } from '@/test/utils'
import type { AskResponse, AskSuggestion, CategorizationSummary } from '@/types'

const api = vi.hoisted(() => ({
  categorization: { summary: vi.fn(), ask: vi.fn(), choose: vi.fn() },
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
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

const cat = (id: string, name: string, group_id: string | null) => ({
  id, name, group_id, icon: 'circle-help', color: '#10b981', user_id: 'u',
  is_system: false, is_hidden: false, treat_as_transfer: false, is_ignored: false,
})
const garden = cat('c9', 'Garden', 'g3')
const groupsFixture = [
  { id: 'g3', name: 'Home', icon: 'x', color: '#000', user_id: 'u', position: 0, is_system: false, is_hidden: false, categories: [garden] },
]

function summary(overrides: Partial<CategorizationSummary> = {}): CategorizationSummary {
  return {
    pending_groups: 1, pending_transactions: 3, applied_last_7d: 0, last_run: null,
    ai_available: true, ai_enabled: true, month_cost_usd: '0', monthly_cap_usd: '2', ...overrides,
  }
}

const newSuggestion: AskSuggestion = {
  category_id: null,
  new_category: { group: 'Casa', name: 'Jardinagem' },
  group_exists: false,
  confidence: 88,
  applies_to: 3,
}

function reply(overrides: Partial<AskResponse> = {}): AskResponse {
  return {
    reply: 'Entendi: é o jardineiro.',
    suggestion: newSuggestion,
    note: 'Aurino: jardineiro semanal',
    assistant_turn: '{"reply":"Entendi: é o jardineiro."}',
    person_messages_left: 29,
    ...overrides,
  }
}

function apiError(status: number, detail: unknown) {
  const headers = new AxiosHeaders()
  return new AxiosError('failed', String(status), undefined, undefined, {
    status, statusText: '', headers, config: { headers }, data: { detail },
  })
}

const onAccepted = vi.fn()

function target(overrides: Partial<AskTarget> = {}): AskTarget {
  return {
    merchantKey: 'K',
    storeName: 'Aurino Silva',
    count: 3,
    total: 450,
    currency: 'BRL',
    lastDate: '2026-10-15',
    samples: ['PIX AURINO SILVA', 'PIX AURINO S', 'PIX AURINO', 'PIX EXTRA'],
    suggestedGroupId: null,
    onAccepted,
    ...overrides,
  }
}

function Opener({ of }: { of: AskTarget }) {
  const ask = useAsk()
  return (
    <button type="button" onClick={() => ask.open(of)}>
      open {of.storeName}
    </button>
  )
}

async function renderDialog(of: AskTarget = target()) {
  const utils = renderWithProviders(
    <AskProvider>
      <Opener of={of} />
    </AskProvider>,
  )
  await utils.user.click(screen.getByRole('button', { name: `open ${of.storeName}` }))
  await screen.findByRole('dialog')
  return utils
}

const box = () => screen.getByRole('textbox', { name: t('categorization.ask.message') })

beforeEach(() => {
  vi.clearAllMocks()
  workspace.canWrite = true
  api.categorization.summary.mockResolvedValue(summary())
  api.categorization.ask.mockResolvedValue(reply())
  api.categorization.choose.mockResolvedValue({
    category_id: 'new-cat', created_group: true, created_category: true, applied: 3, event_ids: ['e1'],
  })
  api.categories.list.mockResolvedValue([garden])
  api.categoryGroups.list.mockResolvedValue(groupsFixture)
})

describe('AskDialog', () => {
  it('mostra a loja com contexto e até 3 exemplos', async () => {
    await renderDialog()
    const dialog = screen.getByRole('dialog', { name: t('categorization.ask.title', { name: 'Aurino Silva' }) })
    expect(dialog).toHaveTextContent('3 transactions')
    expect(dialog).toHaveTextContent('R$450.00')
    expect(within(dialog).getByText('PIX AURINO SILVA')).toBeInTheDocument()
    expect(within(dialog).queryByText('PIX EXTRA')).not.toBeInTheDocument()
    expect(box()).toHaveFocus()
  })

  it('Enter envia a conversa e a segunda leva o turno do assistente', async () => {
    const { user } = await renderDialog()
    await user.type(box(), 'é o jardineiro{Enter}')
    expect(api.categorization.ask).toHaveBeenCalledWith({
      merchant_key: 'K',
      messages: [{ role: 'user', content: 'é o jardineiro' }],
    })
    expect(await screen.findByText('Entendi: é o jardineiro.')).toBeInTheDocument()
    expect(box()).toHaveValue('')

    await user.type(box(), 'toda semana{Enter}')
    await waitFor(() => expect(api.categorization.ask).toHaveBeenCalledTimes(2))
    expect(api.categorization.ask).toHaveBeenLastCalledWith({
      merchant_key: 'K',
      messages: [
        { role: 'user', content: 'é o jardineiro' },
        { role: 'assistant', content: '{"reply":"Entendi: é o jardineiro."}' },
        { role: 'user', content: 'toda semana' },
      ],
    })
  })

  it('Shift+Enter quebra a linha sem enviar', async () => {
    const { user } = await renderDialog()
    await user.type(box(), 'linha 1{Shift>}{Enter}{/Shift}linha 2')
    expect(box()).toHaveValue('linha 1\nlinha 2')
    expect(api.categorization.ask).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: t('categorization.ask.send') }))
    expect(api.categorization.ask).toHaveBeenCalledWith(
      expect.objectContaining({ messages: [{ role: 'user', content: 'linha 1\nlinha 2' }] }),
    )
  })

  it('cartão da sugestão nova: aplica nas 3, Aceitar cria com a nota e fecha', async () => {
    const { user } = await renderDialog()
    await user.type(box(), 'é o jardineiro{Enter}')
    const card = await screen.findByTestId('ask-suggestion')
    expect(card).toHaveTextContent('Casa › Jardinagem')
    expect(card).toHaveTextContent(t('categorization.ask.newCategory'))
    expect(card).toHaveTextContent(t('categorization.ask.newGroup'))
    expect(card).toHaveTextContent(t('categorization.ask.confidence', { value: 88 }))
    expect(card).toHaveTextContent(t('categorization.ask.appliesTo', { count: 3 }))

    await user.click(within(card).getByRole('button', { name: t('categorization.ask.accept') }))
    expect(api.categorization.choose).toHaveBeenCalledWith({
      merchant_key: 'K',
      new_category: { group: 'Casa', name: 'Jardinagem' },
      note: 'Aurino: jardineiro semanal',
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(onAccepted).toHaveBeenCalledWith(expect.objectContaining({ applied: 3, category_id: 'new-cat' }))
  })

  it('categoria existente sem nota nova: só category_id e as linhas do alvo', async () => {
    api.categorization.ask.mockResolvedValue(
      reply({
        note: null,
        suggestion: { category_id: 'c9', new_category: null, group_exists: true, confidence: 95, applies_to: 1 },
      }),
    )
    const { user } = await renderDialog(target({ transactionIds: ['t1'] }))
    await user.type(box(), 'jardim{Enter}')
    const card = await screen.findByTestId('ask-suggestion')
    expect(card).toHaveTextContent('Home › Garden')
    expect(card).not.toHaveTextContent(t('categorization.ask.newCategory'))
    expect(api.categorization.ask).toHaveBeenCalledWith(expect.objectContaining({ transaction_ids: ['t1'] }))
    await user.click(within(card).getByRole('button', { name: t('categorization.ask.accept') }))
    expect(api.categorization.choose).toHaveBeenCalledWith({
      merchant_key: 'K',
      transaction_ids: ['t1'],
      category_id: 'c9',
    })
  })

  it('erro ao aceitar avisa no diálogo e não fecha', async () => {
    api.categorization.choose.mockRejectedValue(apiError(404, { code: 'not_found', message: 'Nenhuma transação pendente' }))
    const { user } = await renderDialog()
    await user.type(box(), 'é o jardineiro{Enter}')
    const card = await screen.findByTestId('ask-suggestion')
    await user.click(within(card).getByRole('button', { name: t('categorization.ask.accept') }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Nenhuma transação pendente')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(onAccepted).not.toHaveBeenCalled()
  })

  it('reabrir a mesma loja mostra a conversa; aceitar apaga', async () => {
    const { user } = await renderDialog()
    await user.type(box(), 'é o jardineiro{Enter}')
    await screen.findByText('Entendi: é o jardineiro.')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'open Aurino Silva' }))
    expect(await screen.findByText('Entendi: é o jardineiro.')).toBeInTheDocument()
    expect(screen.getByText('é o jardineiro')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('categorization.ask.accept') }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'open Aurino Silva' }))
    await screen.findByRole('dialog')
    expect(screen.queryByText('Entendi: é o jardineiro.')).not.toBeInTheDocument()
  })

  it('com 30 mensagens da pessoa o campo fica desabilitado', async () => {
    api.categorization.ask.mockResolvedValue(reply({ suggestion: null, reply: 'ok' }))
    const { user } = await renderDialog()
    for (let i = 1; i <= 30; i++) {
      await user.type(box(), `m${i}{Enter}`)
      await waitFor(() => expect(api.categorization.ask).toHaveBeenCalledTimes(i))
      await waitFor(() => expect(screen.getAllByText('ok')).toHaveLength(i))
    }
    await waitFor(() => expect(box()).toBeDisabled())
    expect(screen.getByText(t('categorization.ask.limitReached', { max: 30 }))).toBeInTheDocument()
    expect(screen.getByText(t('categorization.ask.counter', { used: 30, max: 30 }))).toBeInTheDocument()
  }, 20_000)

  it('504 mostra a mensagem de timeout e devolve o texto ao campo', async () => {
    api.categorization.ask.mockRejectedValue(apiError(504, { code: 'timeout', message: 'x' }))
    const { user } = await renderDialog()
    await user.type(box(), 'é o jardineiro{Enter}')
    expect(await screen.findByRole('alert')).toHaveTextContent(t('categorization.ask.errors.timeout'))
    expect(box()).toHaveValue('é o jardineiro')
    expect(screen.getByText(t('categorization.ask.counter', { used: 0, max: 30 }))).toBeInTheDocument()
  })

  it('a resposta nova entra numa região viva', async () => {
    const { user } = await renderDialog()
    const log = screen.getByRole('log')
    await user.type(box(), 'é o jardineiro{Enter}')
    expect(await within(log).findByText('Entendi: é o jardineiro.')).toBeInTheDocument()
  })
})
