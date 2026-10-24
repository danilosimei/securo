import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import DashboardPage from '@/pages/dashboard'
import { TooltipProvider } from '@/components/ui/tooltip'
import { formatCurrency } from '@/lib/format'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, DashboardSummary } from '@/types'

const api = vi.hoisted(() => ({
  dashboard: {
    summary: vi.fn(),
    spendingByCategory: vi.fn(),
    balanceHistory: vi.fn(),
    projectedTransactions: vi.fn(),
  },
  transactions: { list: vi.fn(), calendar: vi.fn(), update: vi.fn() },
  budgets: { comparison: vi.fn() },
  categories: { list: vi.fn() },
  categoryGroups: { list: vi.fn() },
  accounts: { list: vi.fn() },
  goals: { summary: vi.fn() },
  groups: { list: vi.fn() },
  payees: { list: vi.fn() },
  rules: { create: vi.fn() },
  categorization: { summary: vi.fn() },
  admin: { accountingMode: vi.fn().mockResolvedValue({ mode: 'cash' }) },
}))

const notifyCorrection = vi.hoisted(() => vi.fn())

vi.mock('@/lib/api', () => api)

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
    transaction: unknown
    onSave: (data: { category_id: string | null }) => void
  }) =>
    open && transaction ? <button onClick={() => onSave({ category_id: 'c2' })}>save c2</button> : null,
}))

vi.mock('@/contexts/workspace-context', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/workspace-context')>()),
  useWorkspace: () => ({ canWrite: true, canManage: true }),
}))

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null, activeWalletIds: null }),
}))

function account(overrides: Partial<Account>): Account {
  return {
    id: 'acc',
    user_id: 'user-1',
    connection_id: null,
    external_id: null,
    name: 'Account',
    display_name: null,
    masked_number: null,
    institution_name: null,
    institution_logo_url: null,
    type: 'checking',
    balance: 0,
    current_balance: 0,
    previous_balance: null,
    balance_primary: null,
    currency: 'USD',
    credit_limit: null,
    available_credit: null,
    statement_close_day: null,
    payment_due_day: null,
    next_close_date: null,
    next_due_date: null,
    minimum_payment: null,
    card_brand: null,
    card_level: null,
    shared_balance_group: null,
    is_closed: false,
    closed_at: null,
    ...overrides,
  }
}

// Two cards on one shared credit line both report the line's full -300.
const accounts = [
  account({ id: 'checking', name: 'Checking', current_balance: 1000 }),
  account({ id: 'card-a', name: 'Card A', type: 'credit_card', current_balance: -300, shared_balance_group: 'line-1' }),
  account({ id: 'card-b', name: 'Card B', type: 'credit_card', current_balance: -300, shared_balance_group: 'line-1' }),
  account({ id: 'card-c', name: 'Card C', type: 'credit_card', current_balance: -200 }),
]

const summary: DashboardSummary = {
  total_balance: { USD: 500 },
  total_balance_primary: 500,
  projected_balance: { USD: 500 },
  projected_balance_primary: 500,
  balance_date: '2026-09-12',
  monthly_income: 0,
  monthly_expenses: 0,
  monthly_income_primary: 0,
  monthly_expenses_primary: 0,
  accounts_count: accounts.length,
  pending_categorization: 0,
  pending_categorization_amount: 0,
  assets_value: {},
  assets_value_primary: 0,
  primary_currency: 'USD',
  pending_shares_net: 0,
}

beforeEach(() => {
  vi.clearAllMocks()
  api.dashboard.summary.mockResolvedValue(summary)
  api.dashboard.spendingByCategory.mockResolvedValue([])
  api.dashboard.balanceHistory.mockResolvedValue({ current: [], previous: [] })
  api.dashboard.projectedTransactions.mockResolvedValue([])
  api.transactions.list.mockResolvedValue({ items: [], total: 0 })
  api.budgets.comparison.mockResolvedValue([])
  api.categories.list.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue(accounts)
  api.goals.summary.mockResolvedValue([])
  api.categorization.summary.mockResolvedValue({
    pending_groups: 2, pending_transactions: 5, applied_last_7d: 47, last_run: null,
    ai_available: true, month_cost_usd: 0, monthly_cap_usd: 1,
  })
  api.groups.list.mockResolvedValue([])
  api.payees.list.mockResolvedValue([])
})

describe('Dashboard net worth breakdown', () => {
  it('counts a shared credit line once in the credit card total', async () => {
    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )

    await screen.findByText(formatCurrency(500, 'USD', 'en-US'))
    await user.hover(screen.getByRole('button', { name: 'i' }))

    const tooltip = await screen.findByRole('tooltip')
    const cardRow = within(tooltip).getByText(t('dashboard.creditCardBalance')).parentElement!
    expect(cardRow).toHaveTextContent(formatCurrency(-500, 'USD', 'en-US'))
  })
})

describe('Dashboard categorization card', () => {
  it('replaces the uncategorized banner with the review card', async () => {
    renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )
    const card = await screen.findByTestId('categorization-card-pending')
    expect(card).toHaveTextContent('5 to review')
    expect(card).toHaveAttribute('href', '/categorization?tab=review')
  })

  it('shows the green state when nothing waits for review', async () => {
    api.categorization.summary.mockResolvedValue({
      pending_groups: 0, pending_transactions: 0, applied_last_7d: 47, last_run: null,
      ai_available: true, month_cost_usd: 0, monthly_cap_usd: 1,
    })
    renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )
    expect(await screen.findByTestId('categorization-card-clear')).toBeInTheDocument()
    expect(screen.queryByTestId('categorization-card-pending')).not.toBeInTheDocument()
  })

  it('warns about rows in another currency with no rate, only when there are some', async () => {
    api.dashboard.summary.mockResolvedValue({ ...summary, unconverted_count: 2 })
    const { unmount } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )
    expect(await screen.findByTestId('dashboard-unconverted')).toHaveTextContent(
      t('reports.explore.unconverted', { n: 2 }),
    )
    unmount()

    api.dashboard.summary.mockResolvedValue(summary)
    renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )
    await screen.findByTestId('categorization-card-pending')
    expect(screen.queryByTestId('dashboard-unconverted')).not.toBeInTheDocument()
  })

  it('falls back to the uncategorized banner when AI is off and opens its drill-down', async () => {
    api.categorization.summary.mockResolvedValue({
      pending_groups: 0, pending_transactions: 0, applied_last_7d: 0, last_run: null,
      ai_available: false, month_cost_usd: 0, monthly_cap_usd: 1,
    })
    api.dashboard.summary.mockResolvedValue({
      ...summary, pending_categorization: 3, pending_categorization_amount: 90,
    })
    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )
    const banner = await screen.findByTestId('categorization-card-legacy')
    expect(screen.queryByTestId('categorization-card-pending')).not.toBeInTheDocument()
    await user.click(within(banner).getAllByRole('button')[0])
    await waitFor(() =>
      expect(api.transactions.list).toHaveBeenCalledWith(expect.objectContaining({ uncategorized: true })),
    )
  })

  it('hands a category fixed from the drill-down to the correction toast', async () => {
    api.categorization.summary.mockResolvedValue({
      pending_groups: 0, pending_transactions: 0, applied_last_7d: 0, last_run: null,
      ai_available: false, month_cost_usd: 0, monthly_cap_usd: 1,
    })
    api.dashboard.summary.mockResolvedValue({ ...summary, pending_categorization: 1 })
    api.transactions.list.mockResolvedValue({
      items: [{
        id: 'tx-1', account_id: 'checking', description: 'IFOOD *PEDIDO', amount: 30, currency: 'USD',
        date: '2026-10-18', type: 'debit', status: 'posted', source: 'sync',
        category_id: null, category: null, splits: [],
      }],
      total: 1,
    })
    api.transactions.update.mockResolvedValue({})
    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )
    const banner = await screen.findByTestId('categorization-card-legacy')
    await user.click(within(banner).getAllByRole('button')[0])
    // The recent list shows the same row: click the one inside the drill-down panel.
    const panel = (await screen.findByRole('heading', { name: t('dashboard.drillDownUncategorized') })).closest('.fixed')!
    await user.click(await within(panel as HTMLElement).findByText('IFOOD *PEDIDO'))
    await user.click(await screen.findByRole('button', { name: 'save c2' }))
    await waitFor(() => expect(notifyCorrection).toHaveBeenCalledWith({ id: 'tx-1' }))
    expect(api.transactions.update).toHaveBeenCalledWith('tx-1', { category_id: 'c2' })
  })
})

describe('Dashboard month', () => {
  it('sits in the header where Reports puts its period, with the same control', async () => {
    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
      { route: '/?month=2026-09' },
    )
    await waitFor(() => expect(api.dashboard.summary).toHaveBeenCalledWith('2026-09-01', undefined, undefined, undefined))

    const slot = screen.getByTestId('page-period')
    // Right of the title from lg up; under it, full width, on a phone.
    expect(slot).toHaveClass('w-full', 'sm:ml-auto', 'sm:w-auto')
    expect(slot.parentElement).toHaveClass('flex-col', 'lg:flex-row', 'lg:justify-between')
    expect(slot.parentElement).toContainElement(screen.getByRole('heading', { level: 1 }))
    const group = within(slot).getByRole('group', { name: t('reports.explore.periodGroup') })
    expect(within(group).getByTestId('period-menu')).toHaveTextContent('September 2026')

    await user.click(within(group).getByRole('button', { name: t('common.nextMonth') }))
    await waitFor(() => expect(api.dashboard.summary).toHaveBeenCalledWith('2026-10-01', undefined, undefined, undefined))
    expect(screen.getAllByTestId('page-period')).toHaveLength(1)
  })
})
