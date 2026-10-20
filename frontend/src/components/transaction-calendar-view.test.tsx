import { describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

import { TransactionCalendarView } from '@/components/transaction-calendar-view'
import { renderWithProviders, t } from '@/test/utils'
import type { TransactionCalendarDay, TransactionCalendarItem, TransactionCalendarResponse } from '@/types'

vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => 'UTC' }))

const DAY = '2026-09-10'

function item(overrides: Partial<TransactionCalendarItem> & { id: string; description: string }): TransactionCalendarItem {
  return {
    kind: 'actual',
    recurring_id: null,
    date: DAY,
    amount: 10,
    amount_primary: null,
    currency: 'BRL',
    type: 'debit',
    account_id: null,
    account_name: null,
    category_id: null,
    category_name: null,
    category_icon: null,
    category_color: null,
    status: 'posted',
    source: 'sync',
    transfer_pair_id: null,
    is_transfer: false,
    is_ignored: false,
    exclude_from_pnl: false,
    category_source: null,
    categorization_pending: false,
    ...overrides,
  }
}

function day(items: TransactionCalendarItem[]): TransactionCalendarDay {
  return {
    date: DAY,
    in_month: true,
    ending_balance: 0,
    income: 0,
    expense: 30,
    transfer_net: 0,
    actual_income: 0,
    actual_expense: 30,
    actual_transfer_net: 0,
    projected_income: 0,
    projected_expense: 0,
    projected_transfer_net: 0,
    actual_count: items.length,
    projected_count: 0,
    has_income: false,
    has_expense: true,
    has_transfer: false,
    items,
  }
}

function renderCalendar(items: TransactionCalendarItem[]) {
  const calendar: TransactionCalendarResponse = {
    month: '2026-09',
    currency: 'BRL',
    account_ids: null,
    days: [day(items)],
  }
  return renderWithProviders(
    <TransactionCalendarView
      calendar={calendar}
      isLoading={false}
      locale="en-US"
      dateLocale="en-US"
      mask={(value) => value}
      selectedDate={DAY}
      onSelectedDateChange={() => {}}
      onOpenTransaction={() => {}}
      userCurrency="BRL"
    />,
  )
}

/** The selected day's rows for `description` (the day cells preview it too,
 *  and the panel renders once per layout): the buttons that open it. */
const rows = (description: string) =>
  screen
    .getAllByText(description)
    .map((el) => el.closest('button'))
    .filter((el): el is HTMLButtonElement => el !== null)

const sealed = (description: string, label: string) =>
  rows(description).some((el) => within(el).queryByLabelText(label) !== null)

describe('TransactionCalendarView origin seal', () => {
  it('shows the same seal as the lists: origin, or review when a suggestion waits', () => {
    renderCalendar([
      item({ id: 'a', description: 'IFOOD', category_source: 'ai', category_name: 'Food' }),
      item({ id: 'b', description: 'PADARIA', category_source: 'none', categorization_pending: true }),
      item({ id: 'c', description: 'POSTO', category_source: 'user', category_name: 'Fuel' }),
    ])

    const ai = t('categorization.origin.ai')
    const review = t('categorization.origin.reviewAria')
    expect(sealed('IFOOD', ai)).toBe(true)
    expect(sealed('PADARIA', review)).toBe(true)
    expect(sealed('POSTO', ai) || sealed('POSTO', review)).toBe(false)
    // The rows are buttons already: no link nested inside any of them.
    expect(screen.queryAllByRole('link')).toEqual([])
  })
})
