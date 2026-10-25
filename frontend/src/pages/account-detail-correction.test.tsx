/** Account detail hands a category fix to the correction toast, and only a
 *  real fix: an unchanged save keeps the plain confirmation. Mirrors the
 *  transactions page test. */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import AccountDetailPage from '@/pages/account-detail'
import { renderWithProviders, t } from '@/test/utils'
import type { Transaction } from '@/types'

const account = {
  id: 'acc-1', name: 'Checking', type: 'checking', currency: 'BRL',
  balance: 150, current_balance: 150, is_active: true,
}
const tx = {
  id: 'tx-1', user_id: 'user-1', account_id: 'acc-1', description: 'IFOOD *PEDIDO',
  amount: 30, currency: 'BRL', date: '2026-10-18', type: 'debit', status: 'posted',
  source: 'sync', category_id: 'c1', category_source: 'ai', payee_id: null,
  notes: null, splits: [], is_ignored: false,
} as unknown as Transaction

const api = vi.hoisted(() => ({
  accounts: { get: vi.fn(), bills: vi.fn(), summary: vi.fn(), list: vi.fn(), update: vi.fn() },
  transactions: { list: vi.fn(), update: vi.fn(), delete: vi.fn(), create: vi.fn() },
  dashboard: { projectedTransactions: vi.fn() },
  categories: { list: vi.fn() },
  categoryGroups: { list: vi.fn() },
  payees: { list: vi.fn() },
}))
const notifyCorrection = vi.hoisted(() => vi.fn())
const toast = vi.hoisted(() => Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), info: vi.fn() }))

vi.mock('@/lib/api', () => api)
vi.mock('sonner', () => ({ toast }))
vi.mock('@/hooks/use-category-correction-toast', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/use-category-correction-toast')>()),
  useCategoryCorrectionToast: () => ({ notifyCorrection }),
}))
// The real dialog is covered on its own; here only its callbacks matter.
vi.mock('@/components/transaction-dialog', () => ({
  TransactionDialog: ({
    open, transaction, onSave,
  }: {
    open: boolean
    transaction: Transaction | null
    onSave: (data: { category_id: string | null }) => void
  }) =>
    open && transaction ? (
      <div>
        <button onClick={() => onSave({ category_id: 'c2' })}>save c2</button>
        <button onClick={() => onSave({ category_id: transaction.category_id })}>save same</button>
      </div>
    ) : null,
}))
vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => false }))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '***' }),
}))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'BRL' } } }),
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  api.accounts.get.mockResolvedValue(account)
  api.accounts.list.mockResolvedValue([account])
  api.accounts.summary.mockResolvedValue({
    opening_balance: 0, current_balance: 150, monthly_income: 0, monthly_expenses: 0,
    projected_income: 0, projected_expenses: 0,
  })
  api.transactions.list.mockResolvedValue({ items: [tx], total: 1 })
  api.transactions.update.mockImplementation(async (id: string, data: object) => ({ ...tx, id, ...data }))
  api.dashboard.projectedTransactions.mockResolvedValue([])
  api.categories.list.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.payees.list.mockResolvedValue([])
})

async function openRow() {
  const result = renderWithProviders(<AccountDetailPage />, {
    route: '/accounts/acc-1', path: '/accounts/:id',
  })
  await result.user.click(await screen.findByText('IFOOD *PEDIDO'))
  return result
}

describe('account detail: correction toast', () => {
  it('saves the new category and hands it to the correction toast, once', async () => {
    const { user } = await openRow()
    await user.click(screen.getByRole('button', { name: 'save c2' }))
    await waitFor(() => expect(notifyCorrection).toHaveBeenCalledTimes(1))
    expect(api.transactions.update).toHaveBeenCalledWith('tx-1', { category_id: 'c2' }, expect.any(String))
    const actionId = api.transactions.update.mock.calls[0][2]
    expect(notifyCorrection).toHaveBeenCalledWith({ id: 'tx-1' }, { actionId })
    expect(toast.success).not.toHaveBeenCalledWith(t('accounts.updated'))
  })

  it('keeps the plain confirmation when the category did not change', async () => {
    const { user } = await openRow()
    await user.click(screen.getByRole('button', { name: 'save same' }))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(t('accounts.updated')))
    expect(notifyCorrection).not.toHaveBeenCalled()
  })
})
