import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import { OverviewTab } from '@/components/reports/overview/overview-tab'
import { renderWithProviders, t } from '@/test/utils'
import { WithPeriodBar } from '@/test/report-period'
import { GROUPS, overviewFixture } from '@/test/reports-fixtures'

const api = vi.hoisted(() => ({
  reports: { overview: vi.fn() },
  categoryGroups: { list: vi.fn() },
}))

vi.mock('@/lib/api', () => ({ reports: api.reports, categoryGroups: api.categoryGroups }))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'BRL' } } }),
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

const spark = Array.from({ length: 12 }, (_, i) => String(100 + i * 20))

function withRepeats() {
  return overviewFixture({
    repeats: [
      {
        merchant: 'BAKER BAR', label: 'Baker Bar', category_id: 'c-market', category: 'Mercado', group_id: 'g-food',
        times: 10, months: 4, per_month: 2.5, month_total: '280.00', year_total: '3360.00', avg_purchase: '112.00', sparkline: spark,
      },
      {
        merchant: 'PADOCA', label: 'Padoca', category_id: null, category: 'Sem categoria', group_id: null,
        times: 6, months: 3, per_month: 1.5, month_total: '20.00', year_total: '240.00', avg_purchase: '10.00', sparkline: spark,
      },
    ],
    repeat_categories: [
      { category_id: 'c-market', category: 'Mercado', group_id: 'g-food', times: 120, per_month: 20, month_total: '2200.00' },
    ],
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  api.categoryGroups.list.mockResolvedValue(GROUPS)
})

describe('Overview: what keeps coming back', () => {
  it('lists the stores you go back to, how often and how much, each opening the store', async () => {
    api.reports.overview.mockResolvedValue(withRepeats())
    renderWithProviders(<WithPeriodBar><OverviewTab /></WithPeriodBar>, { route: '/reports?tab=overview&period=last_month' })

    const block = await screen.findByTestId('block-repeats')
    expect(block).toHaveTextContent(t('reports.overview.repeats.title'))
    expect(block).toHaveTextContent(t('reports.overview.repeats.sum', { value: 'R$300' }))
    const [baker, padoca] = within(block).getAllByTestId('repeat-store')
    expect(baker).toHaveTextContent('Baker Bar')
    expect(baker).toHaveTextContent(t('reports.overview.repeats.times', { times: 10, months: 4 }))
    expect(baker).toHaveTextContent(t('reports.overview.repeats.perMonth', { n: '2.5' }))
    expect(baker).toHaveTextContent(t('reports.overview.repeats.monthly', { value: 'R$280' }))
    expect(baker).toHaveTextContent(t('reports.overview.repeats.inPeriod', { value: 'R$1,120' }))
    await waitFor(() => expect(api.categoryGroups.list).toHaveBeenCalled())
    expect(baker).toHaveAttribute(
      'href',
      '/reports?tab=explore&period=last_month&group=g-food&category=c-market&merchant=BAKER+BAR',
    )
    expect(padoca.getAttribute('href')).toContain('category=none&merchant=PADOCA')
    expect(within(block).getByTestId('repeats-all')).toHaveAttribute('href', '/reports?tab=explore&period=last_month&view=merchant')
    expect(within(block).getByTestId('repeat-categories')).toHaveTextContent(
      t('reports.overview.repeats.categoryLine', { n: '20', value: 'R$2,200' }),
    )
  })

  it('is not shown when nothing repeats', async () => {
    api.reports.overview.mockResolvedValue(overviewFixture({ repeats: [], repeat_categories: [] }))
    renderWithProviders(<WithPeriodBar><OverviewTab /></WithPeriodBar>, { route: '/reports?tab=overview&period=last_month' })
    await screen.findByTestId('highlights')
    expect(screen.queryByTestId('block-repeats')).not.toBeInTheDocument()
  })
})
