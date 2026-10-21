import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'

import { TransactionDrillDown } from '@/components/transaction-drill-down'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { admin, dashboard, transactions } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/lib/api', () => ({
  transactions: { list: vi.fn() },
  dashboard: { projectedTransactions: vi.fn() },
  admin: { accountingMode: vi.fn() },
}))

const filter = { title: 'Uncategorized', uncategorized: true }

function renderPanel({ dialogOpen }: { dialogOpen: boolean }) {
  const onClose = vi.fn()
  const result = renderWithProviders(
    <>
      <TransactionDrillDown filter={filter} onClose={onClose} />
      <Dialog open={dialogOpen}>
        <DialogContent>
          <DialogTitle>Edit transaction</DialogTitle>
          <button type="button">Category</button>
        </DialogContent>
      </Dialog>
    </>,
  )
  return { ...result, onClose }
}

// The outside-click listener is attached after a short delay so the click
// that opened the panel does not close it.
async function waitForOutsideClickListener() {
  await new Promise(resolve => setTimeout(resolve, 150))
}

describe('TransactionDrillDown', () => {
  beforeEach(() => {
    vi.mocked(transactions.list).mockResolvedValue({ items: [], total: 0 } as never)
    vi.mocked(dashboard.projectedTransactions).mockResolvedValue([])
    vi.mocked(admin.accountingMode).mockResolvedValue({ mode: 'cash' } as never)
  })

  afterEach(() => {
    vi.resetAllMocks()
  })

  it('closes on a click outside the panel', async () => {
    const { onClose } = renderPanel({ dialogOpen: false })
    await waitForOutsideClickListener()

    fireEvent.mouseDown(document.body)

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('stays open while the user clicks inside a dialog opened from it', async () => {
    const { onClose } = renderPanel({ dialogOpen: true })
    await waitForOutsideClickListener()

    fireEvent.mouseDown(await screen.findByRole('button', { name: 'Category' }))

    expect(onClose).not.toHaveBeenCalled()
  })

  it('leaves Escape to the open dialog', async () => {
    const { onClose } = renderPanel({ dialogOpen: true })
    await screen.findByRole('dialog')

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(onClose).not.toHaveBeenCalled()
  })

  it('closes on Escape when no dialog is open', async () => {
    const { onClose } = renderPanel({ dialogOpen: false })

    fireEvent.keyDown(document, { key: 'Escape' })

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
  })
})

function makeTransactions(count: number, start = 0) {
  return Array.from({ length: count }, (_, i) => ({
    id: `tx-${start + i}`,
    description: `Compra ${start + i}`,
    date: '2026-09-10',
    type: 'debit',
    amount: '10.00',
    amount_primary: null,
    currency: 'USD',
    status: 'posted',
    category: null,
    category_source: null,
    attachment_count: 0,
  }))
}

describe('TransactionDrillDown pagination and server total', () => {
  const monthFilter = {
    title: 'Food - September',
    category_id: 'cat-1',
    type: 'debit' as const,
    from: '2026-09-01',
    to: '2026-09-30',
  }

  beforeEach(() => {
    vi.mocked(transactions.list).mockImplementation((async (params?: { page?: number }) => {
      const page = params?.page ?? 1
      const size = page === 3 ? 20 : 50
      return {
        items: makeTransactions(size, (page - 1) * 50),
        total: 120,
        page,
        limit: 50,
        summary: { income: 200, expense: 1500, net: -1300, excluded: 0, currency: 'USD' },
      }
    }) as never)
    vi.mocked(dashboard.projectedTransactions).mockResolvedValue([])
    vi.mocked(admin.accountingMode).mockResolvedValue({ mode: 'cash' } as never)
  })

  it('shows page 1 with 50 rows and the total the server computed for all 120', async () => {
    renderWithProviders(<TransactionDrillDown filter={monthFilter} onClose={vi.fn()} />)

    expect(await screen.findByText('Compra 0')).toBeInTheDocument()
    expect(screen.getAllByText(/^Compra \d+$/)).toHaveLength(50)
    // expense 1500 minus income 200, not the sum of the 50 rows on screen (500).
    expect(screen.getByTestId('drill-down-total')).toHaveTextContent('$1,300.00')
    expect(screen.getByText('120 transactions')).toBeInTheDocument()
    expect(transactions.list).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, limit: 50, status: 'posted', category_id: 'cat-1', user_pnl_only: true }),
    )
  })

  it('moves between pages with the arrows and keeps the same total', async () => {
    const { user } = renderWithProviders(<TransactionDrillDown filter={monthFilter} onClose={vi.fn()} />)
    await screen.findByText('Compra 0')
    const prev = screen.getByRole('button', { name: 'Previous page' })
    const next = screen.getByRole('button', { name: 'Next page' })
    expect(prev).toBeDisabled()

    await user.click(next)
    expect(await screen.findByText('Compra 50')).toBeInTheDocument()
    expect(screen.queryByText('Compra 0')).not.toBeInTheDocument()
    expect(screen.getByTestId('drill-down-total')).toHaveTextContent('$1,300.00')
    expect(transactions.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }))

    await user.click(next)
    expect(await screen.findByText('Compra 100')).toBeInTheDocument()
    expect(screen.getAllByText(/^Compra \d+$/)).toHaveLength(20)
    expect(next).toBeDisabled()

    await user.click(prev)
    expect(await screen.findByText('Compra 50')).toBeInTheDocument()
  })

  it('reads the income click as income minus expense and uncategorized as expense only', async () => {
    const { unmount } = renderWithProviders(
      <TransactionDrillDown filter={{ ...monthFilter, category_id: undefined, type: 'credit' }} onClose={vi.fn()} />,
    )
    expect(await screen.findByTestId('drill-down-total')).toHaveTextContent('-$1,300.00')
    unmount()

    renderWithProviders(<TransactionDrillDown filter={{ title: 'Sem categoria', uncategorized: true }} onClose={vi.fn()} />)
    expect(await screen.findByTestId('drill-down-total')).toHaveTextContent('$1,500.00')
  })

  it('lists planned transactions apart and leaves them out of the total', async () => {
    vi.mocked(dashboard.projectedTransactions).mockResolvedValue([
      {
        recurring_id: 'rec-1',
        description: 'Aluguel previsto',
        date: '2026-09-28',
        type: 'debit',
        amount: 900,
        amount_primary: null,
        currency: 'USD',
        category_id: 'cat-1',
        category_name: 'Food',
        category_icon: null,
        category_color: null,
      },
    ] as never)
    renderWithProviders(<TransactionDrillDown filter={monthFilter} onClose={vi.fn()} />)

    const planned = await screen.findByRole('region', { name: 'Planned' })
    expect(within(planned).getByText('Aluguel previsto')).toBeInTheDocument()
    expect(screen.getByTestId('drill-down-total')).toHaveTextContent('$1,300.00')
    expect(screen.getByText('120 transactions')).toBeInTheDocument()
  })
})
