/** The transactions page hands a category fix to the correction toast, and
 *  only a real fix: an unchanged save, or one after the dialog undid the
 *  category, keeps the plain confirmation. */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import TransactionsPage from '@/pages/transactions'
import { TooltipProvider } from '@/components/ui/tooltip'
import { renderWithProviders, t } from '@/test/utils'
import type { Transaction } from '@/types'

const tx = {
  id: 'tx-1',
  user_id: 'user-1',
  account_id: 'acc-1',
  description: 'IFOOD *PEDIDO',
  amount: 30,
  currency: 'USD',
  date: '2026-10-18',
  type: 'debit',
  status: 'posted',
  source: 'sync',
  category_id: 'c1',
  category_source: 'ai',
  payee_id: null,
  notes: null,
  is_shared: false,
} as unknown as Transaction

const api = vi.hoisted(() => {
  const fn = () => vi.fn().mockResolvedValue([])
  return {
    transactions: {
      list: vi.fn(), calendar: vi.fn().mockResolvedValue({ days: [] }), update: vi.fn(), get: vi.fn(),
      bulkCategorize: vi.fn(),
    },
    categories: { list: fn(), listIncludingHidden: fn() },
    categoryGroups: { list: fn() },
    accounts: { list: fn() },
    payees: { list: fn() },
    recurring: { list: fn() },
    admin: { accountingMode: vi.fn().mockResolvedValue({ mode: 'cash' }) },
    groups: { list: fn(), get: vi.fn() },
    reconciliation: { suggestions: fn() },
    rules: { create: vi.fn() },
  }
})
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
    open,
    transaction,
    onSave,
    onCategoryUndone,
  }: {
    open: boolean
    transaction: Transaction | null
    onSave: (data: { category_id: string | null }) => void
    onCategoryUndone?: (txId: string, categoryId: string | null, source: string | null) => void
  }) =>
    open && transaction ? (
      <div>
        <button onClick={() => onSave({ category_id: 'c2' })}>save c2</button>
        <button onClick={() => onSave({ category_id: transaction.category_id })}>save same</button>
        <button onClick={() => onCategoryUndone?.(transaction.id, 'c2', 'none')}>undo to c2</button>
      </div>
    ) : null,
}))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))
vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => false }))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { id: 'user-1', preferences: { currency_display: 'USD' } } }),
}))
vi.mock('@/contexts/workspace-context', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/workspace-context')>()),
  useWorkspace: () => ({ canWrite: true, canManage: true, hasModule: () => true }),
}))
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null, activeWalletIds: null }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  api.transactions.list.mockResolvedValue({ items: [tx], total: 1 })
  api.transactions.update.mockImplementation(async (id: string, data: object) => ({ ...tx, id, ...data }))
})

async function openRow() {
  const result = renderWithProviders(
    <TooltipProvider>
      <TransactionsPage />
    </TooltipProvider>,
    { route: '/transactions' },
  )
  await result.user.click(await screen.findByText('IFOOD *PEDIDO'))
  return result
}

describe('transactions page: correction toast', () => {
  it('hands a changed category to the correction toast, once', async () => {
    const { user } = await openRow()
    await user.click(screen.getByRole('button', { name: 'save c2' }))
    await waitFor(() => expect(notifyCorrection).toHaveBeenCalledTimes(1))
    expect(notifyCorrection).toHaveBeenCalledWith({ id: 'tx-1' })
    expect(toast.success).not.toHaveBeenCalledWith(t('transactions.updated'))
  })

  it('keeps the plain confirmation when the category did not change', async () => {
    const { user } = await openRow()
    await user.click(screen.getByRole('button', { name: 'save same' }))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(t('transactions.updated')))
    expect(notifyCorrection).not.toHaveBeenCalled()
  })

  it('offers nothing after the dialog undid the category and the person saves it as is', async () => {
    const { user } = await openRow()
    await user.click(screen.getByRole('button', { name: 'undo to c2' }))
    // The form now shows the restored c2; saving it is not a correction.
    await user.click(screen.getByRole('button', { name: 'save c2' }))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(t('transactions.updated')))
    expect(notifyCorrection).not.toHaveBeenCalled()
  })
})
