import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, screen, waitFor } from '@testing-library/react'

import ReportsPage from '@/pages/reports'
import { renderWithProviders, t } from '@/test/utils'
import type { ReportResponse } from '@/types'

const api = vi.hoisted(() => ({
  reports: {
    netWorth: vi.fn(),
    incomeExpenses: vi.fn(),
    cashFlow: vi.fn(),
  },
  // The page reads the workspace timezone to tell a balance that is already
  // negative from one that crosses later. Unresolved here, which lands on the
  // browser's own day.
  timezones: { list: vi.fn().mockResolvedValue({ timezones: [], default: null }) },
}))

vi.mock('@/lib/api', () => ({ reports: api.reports, timezones: api.timezones }))

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null, activeWalletIds: null }),
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

function emptyReport(type: string): ReportResponse {
  return {
    summary: { primary_value: 1000, change_amount: 100, change_percent: 5, breakdowns: [] },
    trend: [{ date: '2026-01-01', value: 1000, breakdowns: {}, change: null }],
    meta: { type, series_keys: [], currency: 'USD', interval: 'monthly' },
    composition: [],
    category_trend: [],
  }
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 8, 12, 12))
  vi.clearAllMocks()
  api.reports.netWorth.mockResolvedValue(emptyReport('net_worth'))
  api.reports.incomeExpenses.mockResolvedValue(emptyReport('income_expenses'))
  api.reports.cashFlow.mockResolvedValue(emptyReport('cash_flow'))
})

afterEach(() => vi.useRealTimers())

describe('Reports page — Custom range segment', () => {
  it('loads the Net Worth tab with the 1Y preset by default', async () => {
    renderWithProviders(<ReportsPage />)

    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalled())
    const [, , , , , startDate, endDate] = api.reports.netWorth.mock.calls[0]
    expect(startDate).toBeUndefined()
    expect(endDate).toBeUndefined()
  })

  it('opening Custom waits for Apply before querying January 1 through today', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(1))

    await user.click(screen.getByRole('button', { name: t('reports.customRange') }))

    expect(api.reports.netWorth).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: t('reports.range1y') })).toHaveClass('bg-primary')
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    const year = 2026
    await waitFor(() => {
      const last = api.reports.netWorth.mock.calls.at(-1)!
      expect(last[5]).toBe(`${year}-01-01`)
      expect(last[6]).toBe('2026-09-12')
    })

    // The segment itself now displays the picked range instead of "Custom
    // range" — in the same compact, year-less form as the transactions
    // filter bar's applied-range chip.
    const fmt = (iso: string) =>
      new Date(`${iso}T00:00:00`).toLocaleDateString('en-US', {
        day: '2-digit',
        month: 'short',
      })
    expect(
      screen.getByRole('button', { name: t('reports.customRange') }),
    ).toHaveTextContent(`${fmt(`${year}-01-01`)} - ${fmt('2026-09-12')}`)
  })

  it('is not offered on the Cash Flow tab', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: t('reports.cashFlow') }))

    await waitFor(() => expect(api.reports.cashFlow).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: t('reports.customRange') })).not.toBeInTheDocument()
  })
})


it.each(['Cancel', 'Escape', 'outside'] as const)(
  '6M → Custom → %s leaves the preset and query unchanged', async (dismiss) => {
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(1))
    const preset = screen.getByRole('button', { name: t('reports.range6m') })
    await user.click(preset)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(2))
    await user.click(screen.getByRole('button', { name: t('reports.customRange') }))
    if (dismiss === 'Cancel') {
      await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.cancel') }))
    } else if (dismiss === 'Escape') {
      await user.keyboard('{Escape}')
    } else {
      await user.click(document.body)
    }
    expect(preset).toHaveClass('bg-primary')
    expect(api.reports.netWorth).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('reports.customRange') }))
    expect(api.reports.netWorth).toHaveBeenCalledTimes(2)
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(3))
  },
)

it('Reset then Apply restores the fallback for Net Worth', async () => {
  const { user } = renderWithProviders(<ReportsPage />)
  const custom = screen.getByRole('button', { name: t('reports.customRange') })
  await user.click(custom)
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
  await waitFor(() => expect(api.reports.netWorth.mock.calls.at(-1)?.slice(-2)).toEqual([
    '2026-01-01', '2026-09-12',
  ]))
  await user.click(custom)
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.reset') }))
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
  await waitFor(() => {
    expect(screen.getByRole('button', { name: t('reports.range1y') })).toHaveClass('bg-primary')
    expect(api.reports.netWorth.mock.calls.at(-1)?.[0]).toBe(12)
    expect(api.reports.netWorth.mock.calls.at(-1)?.slice(-2)).toEqual([undefined, undefined])
  })
  expect(custom).toHaveTextContent(t('reports.customRange'))
})

const lastIncomeExpensesCall = () => api.reports.incomeExpenses.mock.calls.at(-1)!
// incomeExpenses(months, interval, accountIds, period, days, startDate, endDate)
const periodArg = (call: unknown[]) => call[3]
const datesArg = (call: unknown[]) => call.slice(-2)
const periodButton = (key: string) => screen.getByRole('button', { name: t(`reports.explore.period.${key}`) })

describe('Reports page, closed periods', () => {
  it.each([
    ['reports.incomeExpenses', 'last_12m'],
    ['reports.moneyMap', 'last_3m'],
  ] as const)('%s opens on %s and sends the period to the API', async (tab, fallback) => {
    const { user } = renderWithProviders(<ReportsPage />)
    await user.click(screen.getByRole('button', { name: t(tab) }))

    await waitFor(() => expect(api.reports.incomeExpenses).toHaveBeenCalled())
    expect(periodArg(lastIncomeExpensesCall())).toBe(fallback)
    expect(datesArg(lastIncomeExpensesCall())).toEqual([undefined, undefined])
    expect(periodButton(fallback)).toHaveAttribute('aria-pressed', 'true')
  })

  it('offers exactly the closed shortcuts, not the rolling ones', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await user.click(screen.getByRole('button', { name: t('reports.incomeExpenses') }))

    for (const key of ['this_month', 'last_month', 'last_3m', 'last_6m', 'last_12m', 'ytd']) {
      expect(periodButton(key)).toBeInTheDocument()
    }
    expect(screen.queryByRole('button', { name: t('reports.range2y') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('reports.range30d') })).not.toBeInTheDocument()
  })

  it('picking a shortcut queries that period and shows the real range', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await user.click(screen.getByRole('button', { name: t('reports.incomeExpenses') }))
    await waitFor(() => expect(api.reports.incomeExpenses).toHaveBeenCalled())

    await user.click(periodButton('last_6m'))

    await waitFor(() => expect(periodArg(lastIncomeExpensesCall())).toBe('last_6m'))
    // Today is Sep 12, 2026: six closed months are Mar to Aug.
    expect(screen.getByText(/Mar 26.*Aug 26/)).toBeInTheDocument()
  })

  it('marks this month as partial', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await user.click(screen.getByRole('button', { name: t('reports.incomeExpenses') }))
    await user.click(periodButton('this_month'))

    await waitFor(() => expect(periodArg(lastIncomeExpensesCall())).toBe('this_month'))
    expect(screen.getByText(/Sep 26.*Sep 26/)).toBeInTheDocument()
  })

  it('a custom range sends period=custom with its dates; Reset goes back to the tab fallback', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await user.click(screen.getByRole('button', { name: t('reports.moneyMap') }))
    await waitFor(() => expect(api.reports.incomeExpenses).toHaveBeenCalled())

    const custom = screen.getByRole('button', { name: t('reports.explore.period.custom') })
    await user.click(custom)
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    await waitFor(() => {
      expect(periodArg(lastIncomeExpensesCall())).toBe('custom')
      expect(datesArg(lastIncomeExpensesCall())).toEqual(['2026-01-01', '2026-09-12'])
    })

    await user.click(custom)
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.reset') }))
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    await waitFor(() => {
      expect(periodArg(lastIncomeExpensesCall())).toBe('last_3m')
      expect(datesArg(lastIncomeExpensesCall())).toEqual([undefined, undefined])
    })
    expect(periodButton('last_3m')).toHaveAttribute('aria-pressed', 'true')
  })

  it.each(['1y', '2y', '30d'])('an old saved range (%s) falls back to a valid period', async (rangeKey) => {
    localStorage.setItem(
      'securo.dateRange.reports',
      JSON.stringify({ income_expenses: { rangeKey, from: '', to: '', interval: 'monthly' } }),
    )
    const { user } = renderWithProviders(<ReportsPage />)
    await user.click(screen.getByRole('button', { name: t('reports.incomeExpenses') }))

    await waitFor(() => expect(api.reports.incomeExpenses).toHaveBeenCalled())
    expect(periodArg(lastIncomeExpensesCall())).toBe('last_12m')
    expect(periodButton('last_12m')).toHaveAttribute('aria-pressed', 'true')
  })

  it('a saved closed period is restored', async () => {
    localStorage.setItem(
      'securo.dateRange.reports',
      JSON.stringify({ money_map: { rangeKey: 'last_6m', from: '', to: '', interval: 'monthly' } }),
    )
    const { user } = renderWithProviders(<ReportsPage />)
    await user.click(screen.getByRole('button', { name: t('reports.moneyMap') }))

    await waitFor(() => expect(periodArg(lastIncomeExpensesCall())).toBe('last_6m'))
  })

  it('keeps custom dates across Net Worth, Income & Expenses and Money Map; Cash Flow has none', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await user.click(screen.getByRole('button', { name: t('reports.customRange') }))
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    for (const tab of ['reports.incomeExpenses', 'reports.moneyMap']) {
      await user.click(screen.getByRole('button', { name: t(tab) }))
      await waitFor(() => expect(datesArg(lastIncomeExpensesCall())).toEqual(['2026-01-01', '2026-09-12']))
      expect(periodArg(lastIncomeExpensesCall())).toBe('custom')
    }
    await user.click(periodButton('last_6m'))
    await waitFor(() => expect(datesArg(lastIncomeExpensesCall())).toEqual([undefined, undefined]))
    const count = api.reports.incomeExpenses.mock.calls.length
    const custom = screen.getByRole('button', { name: t('reports.explore.period.custom') })
    await user.click(custom)
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.reset') }))
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.cancel') }))
    expect(api.reports.incomeExpenses).toHaveBeenCalledTimes(count)
    expect(periodButton('last_6m')).toHaveAttribute('aria-pressed', 'true')
    await user.click(custom)
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    await waitFor(() => expect(datesArg(lastIncomeExpensesCall())).toEqual(['2026-01-01', '2026-09-12']))

    await user.click(screen.getByRole('button', { name: t('reports.cashFlow') }))
    await waitFor(() => expect(api.reports.cashFlow).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: t('reports.customRange') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('reports.explore.period.custom') })).not.toBeInTheDocument()
    expect(api.reports.cashFlow.mock.calls.at(-1)?.[0]).toBe(6)
    await user.click(screen.getByRole('button', { name: t('reports.netWorth') }))
    await user.click(screen.getByRole('button', { name: t('reports.customRange') }))
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    await waitFor(() => expect(api.reports.netWorth.mock.calls.at(-1)?.slice(-2))
      .toEqual(['2026-01-01', '2026-09-12']))
  })
})

describe('Reports page — query failure', () => {
  it('surfaces a rejected custom range instead of showing stale numbers', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(1))

    api.reports.netWorth.mockRejectedValueOnce({
      response: { data: { detail: 'Custom range is too wide (max 10 years)' } },
    })
    await user.click(screen.getByRole('button', { name: t('reports.customRange') }))
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))

    expect(await screen.findByText('Custom range is too wide (max 10 years)')).toBeInTheDocument()
    // The prior successful report must not linger under the error.
    expect(screen.queryByText(t('reports.trend'), { exact: false })).not.toBeInTheDocument()
    // Tabs and range controls stay usable so the user can correct the request.
    expect(screen.getByRole('button', { name: t('reports.range1y') })).toBeInTheDocument()
  })

  it('shows a generic fallback for a network failure and recovers via Retry', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(1))

    api.reports.netWorth.mockRejectedValueOnce(new Error('network error'))
    await user.click(screen.getByRole('button', { name: t('reports.range6m') }))

    expect(await screen.findByText(t('reports.loadError'))).toBeInTheDocument()

    api.reports.netWorth.mockResolvedValueOnce(emptyReport('net_worth'))
    await user.click(screen.getByRole('button', { name: t('common.retry') }))

    await waitFor(() => expect(screen.queryByText(t('reports.loadError'))).not.toBeInTheDocument())
  })

  it('recovers by changing the range after a rejected selection', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(1))

    api.reports.netWorth.mockRejectedValueOnce({ response: { data: { detail: 'end_date must be on or before today' } } })
    await user.click(screen.getByRole('button', { name: t('reports.customRange') }))
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    expect(await screen.findByText('end_date must be on or before today')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('reports.range6m') }))
    await waitFor(() => expect(screen.queryByText('end_date must be on or before today')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: t('reports.range6m') })).toHaveClass('bg-primary')
  })
})

describe('Reports page, cash-flow runway', () => {
  async function openCashFlow(meta: Partial<ReportResponse['meta']>) {
    const report = emptyReport('cash_flow')
    api.reports.cashFlow.mockResolvedValue({ ...report, meta: { ...report.meta, ...meta } })
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalled())
    await user.click(screen.getByRole('button', { name: t('reports.cashFlow') }))
    return screen.findByTestId('cash-flow-runway')
  }

  it('names the day the balance goes negative and its lowest point', async () => {
    const callout = await openCashFlow({
      runway_date: '2099-10-05',
      lowest_balance: -500,
      lowest_balance_date: '2099-12-20',
    })

    expect(callout).toHaveTextContent(t('reports.runwayNegativeOn', { date: 'Oct 5, 2099' }))
    expect(callout).toHaveTextContent('-$500.00')
    // Only the crossing date is shown: a second date next to the amount reads
    // as part of the same sentence rather than as the bottom of the dip.
    expect(callout).not.toHaveTextContent('Dec 20')
  })

  it('reads today in the workspace timezone, not the browser one', async () => {
    // 23:30 UTC on the 13th is already the 14th fourteen hours ahead, so the
    // same crossing date is "already negative" there and still ahead in UTC.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date('2026-10-13T23:30:00Z'))
    try {
      api.timezones.list.mockResolvedValue({ timezones: [], default: 'Pacific/Kiritimati' })
      const ahead = await openCashFlow({
        runway_date: '2026-10-14',
        lowest_balance: -10,
        lowest_balance_date: '2026-10-14',
      })
      await waitFor(() => expect(ahead).toHaveTextContent(t('reports.runwayAlreadyNegative')))

      cleanup()
      api.timezones.list.mockResolvedValue({ timezones: [], default: 'UTC' })
      const behind = await openCashFlow({
        runway_date: '2026-10-14',
        lowest_balance: -10,
        lowest_balance_date: '2026-10-14',
      })
      await waitFor(() =>
        expect(behind).toHaveTextContent(t('reports.runwayNegativeOn', { date: 'Oct 14' })),
      )
    } finally {
      vi.useRealTimers()
      api.timezones.list.mockResolvedValue({ timezones: [], default: null })
    }
  })

  it('says the balance is already negative rather than naming today', async () => {
    const callout = await openCashFlow({
      runway_date: '2020-01-02',
      lowest_balance: -500,
      lowest_balance_date: '2020-01-31',
    })

    expect(callout).toHaveTextContent(t('reports.runwayAlreadyNegative'))
    expect(callout).not.toHaveTextContent(t('reports.runwayNegativeOn', { date: 'Jan 2' }))
  })

  it.each([
    [400, '$400.00'],
    [0, '$0.00'],
  ])('reports a lowest balance of %d as never going below zero', async (lowest, amount) => {
    const callout = await openCashFlow({
      runway_date: null,
      lowest_balance: lowest,
      lowest_balance_date: '2026-12-01',
    })

    expect(callout).toHaveTextContent(t('reports.lowestBalance'))
    expect(callout).toHaveTextContent(amount)
    expect(callout).toHaveTextContent('Dec 1')
  })

  it('is not shown on the other tabs', async () => {
    api.reports.netWorth.mockResolvedValue({
      ...emptyReport('net_worth'),
      meta: { ...emptyReport('net_worth').meta, lowest_balance: 1, lowest_balance_date: '2026-10-01' },
    })
    renderWithProviders(<ReportsPage />)
    await screen.findAllByText('$1,000.00')

    expect(screen.queryByTestId('cash-flow-runway')).not.toBeInTheDocument()
  })
})
