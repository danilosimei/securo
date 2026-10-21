import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { TransactionDialog } from '@/components/transaction-dialog'
import { categorization, currencies, groups, payees, settings, transactions } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import { WorkspaceContext } from '@/contexts/workspace-context'
import type { RecurringTransaction, Transaction } from '@/types'

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))
vi.mock('@/hooks/use-display-locale', () => ({
  useDateLocale: () => 'en-US',
  useDisplayLocale: () => 'en-US',
}))

afterEach(() => vi.restoreAllMocks())

it.each(['America/Los_Angeles', 'Pacific/Kiritimati'])(
  'keeps a recurring calendar date unchanged in %s',
  async (timezone) => {
    const originalTimezone = process.env.TZ
    const recurringMatch: RecurringTransaction = {
      id: 'recurring-rent',
      user_id: 'synthetic-user',
      account_id: null,
      category_id: null,
      description: 'Rent',
      amount: 100,
      currency: 'USD',
      type: 'debit',
      frequency: 'monthly',
      weekend_adjustment: 'none',
      day_of_month: 8,
      start_date: '2026-01-08',
      end_date: null,
      is_active: true,
      auto_generate: false,
      next_occurrence: '2026-03-08',
      amount_primary: null,
      fx_rate_used: null,
    }
    vi.spyOn(currencies, 'list').mockResolvedValue([])
    vi.spyOn(payees, 'list').mockResolvedValue([])
    vi.spyOn(groups, 'list').mockResolvedValue([])

    try {
      process.env.TZ = timezone
      renderWithProviders(
        <TransactionDialog
          open
          onClose={vi.fn()}
          transaction={null}
          categories={[]}
          categoryGroups={[]}
          accounts={[]}
          recurringMatch={recurringMatch}
          onSave={vi.fn()}
          loading={false}
          error={null}
        />,
      )

      expect(await screen.findByText(t('transactions.recurringInfo', {
        frequency: t('recurring.monthly'),
        next: '3/8/2026',
      }))).toBeInTheDocument()
    } finally {
      if (originalTimezone === undefined) delete process.env.TZ
      else process.env.TZ = originalTimezone
    }
  },
)

describe('how the category got here', () => {
  const props = {
    open: true,
    onClose: vi.fn(),
    categories: [],
    categoryGroups: [],
    accounts: [],
    onSave: vi.fn(),
    loading: false,
    error: null,
  }

  function stubLists() {
    vi.spyOn(currencies, 'list').mockResolvedValue([])
    vi.spyOn(payees, 'list').mockResolvedValue([])
    vi.spyOn(groups, 'list').mockResolvedValue([])
    vi.spyOn(settings, 'attachments').mockResolvedValue({ max_file_size_mb: 10, allowed_types: [] } as never)
    vi.spyOn(transactions.attachments, 'list').mockResolvedValue([])
  }

  it('does not look up the origin of a transaction being created', async () => {
    stubLists()
    const provenance = vi.spyOn(categorization, 'provenance').mockResolvedValue({
      current_source: 'ai', original_description: null, events: [], store_note: null,
    })
    renderWithProviders(<TransactionDialog {...props} transaction={null} />)
    expect(await screen.findByText(t('transactions.addManual'))).toBeInTheDocument()
    expect(provenance).not.toHaveBeenCalled()
  })

  it('shows the origin block under the category of an existing transaction', async () => {
    stubLists()
    const provenance = vi.spyOn(categorization, 'provenance').mockResolvedValue({
      current_source: 'ai',
      original_description: 'IFOOD *PEDIDO 123',
      store_note: null,
      events: [],
    })
    const tx = {
      id: 'tx-1', description: 'iFood', amount: 30, currency: 'USD', date: '2026-10-18',
      type: 'debit', status: 'posted', account_id: 'acc-1', category_id: null,
      category_source: 'ai', source: 'manual',
    } as unknown as Transaction
    renderWithProviders(<TransactionDialog {...props} transaction={tx} />)
    expect(await screen.findByText(t('categorization.provenance.by_ai'))).toBeInTheDocument()
    expect(provenance).toHaveBeenCalledWith('tx-1')
  })

  it('tells the page when the block undid the category', async () => {
    stubLists()
    vi.spyOn(categorization, 'provenance').mockResolvedValue({
      current_source: 'ai',
      original_description: 'IFOOD *PEDIDO 123',
      store_note: null,
      events: [{
        id: 'e1', run_id: 'r1', source: 'ai', previous_source: 'none', previous_category_id: 'c2',
        new_category_id: 'c1', confidence: 94, reason: null, model: null, user_id: null,
        created_at: new Date().toISOString(), undone_at: null,
      }],
    })
    vi.spyOn(categorization, 'undo').mockResolvedValue({ restored: 1, skipped: 0 })
    const onCategoryUndone = vi.fn()
    const tx = {
      id: 'tx-1', description: 'iFood', amount: 30, currency: 'USD', date: '2026-10-18',
      type: 'debit', status: 'posted', account_id: 'acc-1', category_id: 'c1',
      category_source: 'ai', source: 'manual',
    } as unknown as Transaction
    const { user } = renderWithProviders(
      <WorkspaceContext.Provider value={{ canWrite: true } as unknown as React.ContextType<typeof WorkspaceContext>}>
        <TransactionDialog {...props} transaction={tx} onCategoryUndone={onCategoryUndone} />
      </WorkspaceContext.Provider>,
    )
    await user.click(await screen.findByRole('button', { name: t('categorization.provenance.undo') }))
    await vi.waitFor(() => expect(onCategoryUndone).toHaveBeenCalledWith('tx-1', 'c2', 'none'))
  })
})
