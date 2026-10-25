import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import { HistoryTab } from '@/components/categorization/history-tab'
import { renderWithProviders, t } from '@/test/utils'
import type { HistoryAction } from '@/types'

const api = vi.hoisted(() => ({
  categorization: { history: vi.fn(), historyRows: vi.fn(), undo: vi.fn() },
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

const now = new Date()
const line = (over: Partial<HistoryAction> = {}): HistoryAction => ({
  action_id: 'a1',
  at: now.toISOString(),
  who: 'you',
  count: 14,
  standing: 14,
  undone_at: null,
  from_categories: ['Restaurantes'],
  from_total: 1,
  to_categories: ['Delivery'],
  to_total: 1,
  stores: ['iFood'],
  stores_total: 1,
  ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
  workspace.canWrite = true
  api.categorization.history.mockResolvedValue({ items: [line()], total: 1 })
  api.categorization.historyRows.mockResolvedValue({
    items: [
      {
        event_id: 'e1',
        transaction_id: 't1',
        date: '2026-10-18',
        description: 'Ifd*ifood Club',
        amount: '54.90',
        type: 'debit',
        currency: 'BRL',
        from_category: 'Restaurantes',
        to_category: 'Delivery',
        undone_at: null,
        standing: true,
      },
    ],
    total: 1,
  })
  api.categorization.undo.mockResolvedValue({ restored: 14, skipped: 0 })
})

describe('HistoryTab', () => {
  it('shows one line per action: store, from → to, who and how many', async () => {
    renderWithProviders(<HistoryTab />)
    const row = await screen.findByTestId('history-line-a1')
    expect(row).toHaveTextContent('iFood')
    expect(row).toHaveTextContent('Restaurantes')
    expect(row).toHaveTextContent('Delivery')
    expect(row).toHaveTextContent(t('categorization.history.who.you'))
    expect(row).toHaveTextContent(t('categorization.history.count', { count: 14 }))
    expect(screen.getByText(t('categorization.history.today'))).toBeInTheDocument()
  })

  it('filters by who', async () => {
    const { user } = renderWithProviders(<HistoryTab />)
    await screen.findByTestId('history-line-a1')
    await user.click(screen.getByTestId('history-filter-ai'))
    await waitFor(() =>
      expect(api.categorization.history).toHaveBeenLastCalledWith({ who: 'ai', limit: 30, offset: 0 }),
    )
  })

  it('opens a line to list its rows', async () => {
    const { user } = renderWithProviders(<HistoryTab />)
    await user.click(within(await screen.findByTestId('history-line-a1')).getByRole('button', { expanded: false }))
    const rows = await screen.findByTestId('history-rows-a1')
    expect(api.categorization.historyRows).toHaveBeenCalledWith('a1', { limit: 50, offset: 0 })
    expect(rows).toHaveTextContent('Ifd*ifood Club')
  })

  it('opens the line a toast linked to', async () => {
    renderWithProviders(<HistoryTab />, { route: '/categorization?tab=history&action=a1' })
    expect(await screen.findByTestId('history-rows-a1')).toBeInTheDocument()
  })

  it('undoes the whole action', async () => {
    const { user } = renderWithProviders(<HistoryTab />)
    await user.click(await screen.findByTestId('history-undo-a1'))
    await waitFor(() => expect(api.categorization.undo).toHaveBeenCalledWith({ action_id: 'a1' }))
    expect(toast.success).toHaveBeenCalledWith(t('categorization.correction.undone', { count: 14 }))
  })

  it('says how many stayed when some changed since', async () => {
    api.categorization.history.mockResolvedValue({ items: [line({ standing: 11 })], total: 1 })
    api.categorization.undo.mockResolvedValue({ restored: 11, skipped: 3 })
    const { user } = renderWithProviders(<HistoryTab />)
    const button = await screen.findByTestId('history-undo-a1')
    expect(button).toHaveTextContent(t('categorization.history.undoSome', { count: 11 }))
    await user.click(button)
    await waitFor(() =>
      expect(toast.info).toHaveBeenCalledWith(t('categorization.history.partial', { count: 3 })),
    )
  })

  it('offers no undo for what already went back or changed since', async () => {
    api.categorization.history.mockResolvedValue({
      items: [
        line({ action_id: 'a1', standing: 0, undone_at: now.toISOString() }),
        line({ action_id: 'a2', standing: 0 }),
        line({ action_id: 'a3', who: 'undo', standing: 0 }),
      ],
      total: 3,
    })
    renderWithProviders(<HistoryTab />)
    expect(await screen.findByTestId('history-line-a1')).toHaveTextContent(
      t('categorization.history.undoneAt', { time: '' }).trim(),
    )
    expect(screen.getByTestId('history-line-a2')).toHaveTextContent(t('categorization.history.changedSince'))
    expect(screen.getByTestId('history-line-a3')).toHaveTextContent(t('categorization.history.who.undo'))
    expect(screen.queryByTestId(/history-undo-/)).not.toBeInTheDocument()
  })

  it('hides undo from a viewer', async () => {
    workspace.canWrite = false
    renderWithProviders(<HistoryTab />)
    await screen.findByTestId('history-line-a1')
    expect(screen.queryByTestId('history-undo-a1')).not.toBeInTheDocument()
  })

  it('names several categories and stores by count', async () => {
    api.categorization.history.mockResolvedValue({
      items: [
        line({
          who: 'ai',
          stores: ['iFood', 'Uber', 'Amazon'],
          stores_total: 26,
          from_categories: [null],
          to_categories: ['Delivery', 'Uber', 'Compras'],
          to_total: 12,
        }),
      ],
      total: 1,
    })
    renderWithProviders(<HistoryTab />)
    const row = await screen.findByTestId('history-line-a1')
    expect(row).toHaveTextContent('iFood, Uber, Amazon')
    expect(row).toHaveTextContent(t('categorization.history.moreStores', { count: 23 }))
    expect(row).toHaveTextContent(t('categorization.history.noCategory'))
    expect(row).toHaveTextContent(t('categorization.history.categories', { count: 12 }))
  })

  it('says so when there is nothing yet', async () => {
    api.categorization.history.mockResolvedValue({ items: [], total: 0 })
    renderWithProviders(<HistoryTab />)
    expect(await screen.findByText(t('categorization.history.empty'))).toBeInTheDocument()
  })
})
