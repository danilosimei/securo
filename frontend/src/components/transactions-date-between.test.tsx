import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

import { TransactionsFilterBar } from '@/components/transactions-filter-bar'
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { MobileDateView } from '@/components/mobile-transactions-filter-menu'
import { renderWithProviders, t } from '@/test/utils'

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'pt-BR',
  useDateLocale: () => 'pt-BR',
}))

function barProps(overrides: Record<string, unknown> = {}) {
  const noop = vi.fn()
  return {
    searchInput: '', onSearchChange: noop, onSearchSubmit: noop,
    filterAccountIds: [], onAccountIdsChange: noop,
    filterCategoryIds: [], onCategoryIdsChange: noop,
    filterUncategorized: false, onUncategorizedChange: noop,
    filterPayee: '', onPayeeChange: noop,
    filterGroupId: '', onGroupIdChange: noop,
    filterType: '', onTypeChange: noop,
    filterStatus: '', onStatusChange: noop,
    hideIgnored: false, onHideIgnoredChange: noop,
    filterFrom: '', filterTo: '', onDateRangeChange: vi.fn(),
    filterMinAmount: '', filterMaxAmount: '', onAmountRangeChange: noop,
    onClearAll: noop,
    accounts: [], categories: [], categoryGroups: [], payees: [], groups: [],
    ...overrides,
  }
}

describe('active date filter label', () => {
  it('shows a closed range numerically', () => {
    renderWithProviders(
      <TransactionsFilterBar {...barProps({ filterFrom: '2026-02-01', filterTo: '2026-03-15' })} />,
    )
    expect(screen.getAllByText('01/02/26 – 15/03/26').length).toBeGreaterThan(0)
  })

  it('shows one-sided ranges as since / until', () => {
    const { unmount } = renderWithProviders(
      <TransactionsFilterBar {...barProps({ filterFrom: '2026-02-01' })} />,
    )
    expect(screen.getAllByText('since 01/02/26').length).toBeGreaterThan(0)
    unmount()
    renderWithProviders(<TransactionsFilterBar {...barProps({ filterTo: '2026-03-15' })} />)
    expect(screen.getAllByText('until 15/03/26').length).toBeGreaterThan(0)
  })
})

describe('desktop Between dates', () => {
  // Keyboard activation: the submenu item is what a keyboard user reaches too.
  async function chooseBetween(user: { keyboard: (keys: string) => Promise<void> }) {
    const item = await screen.findByRole('menuitem', { name: t('transactions.filtersBar.customRange') })
    item.focus()
    await user.keyboard('{Enter}')
  }

  async function openBetween() {
    const props = barProps()
    const view = renderWithProviders(<TransactionsFilterBar {...props} />)
    await view.user.click(screen.getAllByRole('button', { name: new RegExp(t('transactions.filtersBar.filters')) })[0])
    const triggers = await screen.findAllByRole('menuitem', { name: new RegExp(t('transactions.filtersBar.date')) })
    await view.user.click(triggers.find((el) => el.getAttribute('aria-haspopup') === 'menu')!)
    return { ...view, props }
  }

  it('lists the item right after All and before the presets', async () => {
    await openBetween()
    const items = (await screen.findAllByRole('menuitem')).map((el) => el.textContent ?? '')
    const all = items.findIndex((x) => x === t('transactions.all'))
    const today = items.findIndex((x) => x === t('reports.explore.period.this_month'))
    const custom = items.findIndex((x) => x === t('transactions.filtersBar.customRange'))
    expect(t('transactions.filtersBar.customRange')).toBe('Between dates…')
    expect(custom).toBe(all + 1)
    expect(today).toBeGreaterThan(custom)
  })

  it('applies typed dates with Enter as ISO and blocks To < From', async () => {
    const { user, props } = await openBetween()
    await chooseBetween(user)
    const from = await screen.findByLabelText(t('transactions.filtersBar.fromLabel'), {}, { timeout: 2000 })
    const to = screen.getByLabelText(t('transactions.filtersBar.toLabel'))
    await user.type(from, '01/02/2026')
    await user.type(to, '15/01/2026')
    expect(screen.getByRole('alert')).toHaveTextContent(t('transactions.filtersBar.rangeOrder'))
    expect(screen.getByRole('button', { name: t('transactions.filtersBar.apply') })).toBeDisabled()
    await user.type(to, '{Enter}')
    expect(props.onDateRangeChange).not.toHaveBeenCalled()

    await user.clear(to)
    await user.type(to, '15/03/2026{Enter}')
    expect(props.onDateRangeChange).toHaveBeenCalledWith('2026-02-01', '2026-03-15')
  })

  it('applies with only "From" filled', async () => {
    const { user, props } = await openBetween()
    await chooseBetween(user)
    await user.type(
      await screen.findByLabelText(t('transactions.filtersBar.fromLabel'), {}, { timeout: 2000 }),
      '1/2/26',
    )
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    expect(props.onDateRangeChange).toHaveBeenCalledWith('2026-02-01', '')
  })
})

describe('date shortcuts, the same as Reports', () => {
  afterEach(() => vi.useRealTimers())

  async function openDateMenu(props = barProps()) {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 9, 21, 12))
    const view = renderWithProviders(<TransactionsFilterBar {...props} />)
    await view.user.click(screen.getAllByRole('button', { name: new RegExp(t('transactions.filtersBar.filters')) })[0])
    const triggers = await screen.findAllByRole('menuitem', { name: new RegExp(t('transactions.filtersBar.date')) })
    await view.user.click(triggers.find((el) => el.getAttribute('aria-haspopup') === 'menu')!)
    return { ...view, props }
  }

  it('lists This month, Last month, 3/6/12 months and Year to date after All and Between dates', async () => {
    await openDateMenu()
    const items = (await screen.findAllByRole('menuitem')).map((el) => el.textContent ?? '')
    const start = items.indexOf(t('transactions.filtersBar.customRange'))
    expect(items.slice(start - 1, start + 7)).toEqual([
      t('transactions.all'),
      t('transactions.filtersBar.customRange'),
      t('reports.explore.period.this_month'),
      t('reports.explore.period.last_month'),
      t('reports.explore.period.last_3m'),
      t('reports.explore.period.last_6m'),
      t('reports.explore.period.last_12m'),
      t('reports.explore.period.ytd'),
    ])
  })

  it.each([
    ['this_month', '2026-10-01', '2026-10-21'],
    ['last_month', '2026-09-01', '2026-09-30'],
    ['last_3m', '2026-07-01', '2026-09-30'],
    ['last_6m', '2026-04-01', '2026-09-30'],
    ['last_12m', '2025-10-01', '2026-09-30'],
    ['ytd', '2026-01-01', '2026-10-21'],
  ])('%s applies the same dates as in Reports', async (period, from, to) => {
    const { user, props } = await openDateMenu()
    const item = await screen.findByRole('menuitem', { name: t(`reports.explore.period.${period}`) })
    item.focus()
    await user.keyboard('{Enter}')
    expect(props.onDateRangeChange).toHaveBeenCalledWith(from, to)
  })

  it('marks the shortcut that matches the active range', async () => {
    await openDateMenu(barProps({ filterFrom: '2026-07-01', filterTo: '2026-09-30' }))
    const item = await screen.findByRole('menuitem', { name: t('reports.explore.period.last_3m') })
    expect(item).toHaveClass('bg-primary/5')
  })
})

describe('MobileDateView', () => {
  function renderMobile(onChange = vi.fn(), from = '', to = '') {
    const view = renderWithProviders(
      <DropdownMenu open>
        <DropdownMenuTrigger>open</DropdownMenuTrigger>
        <DropdownMenuContent>
          <MobileDateView
            from={from}
            to={to}
            presets={[{ key: 'today', label: 'Today', from: '2026-03-01', to: '2026-03-01' }]}
            onChange={onChange}
          />
        </DropdownMenuContent>
      </DropdownMenu>,
    )
    return { ...view, onChange }
  }

  it('shows Between dates right after All and applies typed dates', async () => {
    const { user, onChange } = renderMobile()
    const items = (await screen.findAllByRole('menuitem')).map((el) => el.textContent)
    expect(items).toEqual([t('transactions.all'), t('transactions.filtersBar.customRange'), 'Today'])

    await user.click(screen.getByRole('menuitem', { name: t('transactions.filtersBar.customRange') }))
    await user.type(screen.getByLabelText(t('transactions.filtersBar.fromLabel')), '01/02/2026')
    await user.type(screen.getByLabelText(t('transactions.filtersBar.toLabel')), '15/03/2026')
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    expect(onChange).toHaveBeenCalledWith('2026-02-01', '2026-03-15')
  })

  it('opens already expanded for an active custom range', async () => {
    renderMobile(vi.fn(), '2026-02-01', '2026-03-15')
    expect(await screen.findByLabelText(t('transactions.filtersBar.fromLabel'))).toHaveValue('01/02/2026')
    expect(within(screen.getByRole('menu')).getByLabelText(t('transactions.filtersBar.toLabel'))).toHaveValue('15/03/2026')
  })
})
