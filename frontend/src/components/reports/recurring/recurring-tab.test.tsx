import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { useLocation } from 'react-router-dom'

import { RecurringTab } from '@/components/reports/recurring/recurring-tab'
import { renderWithProviders, t } from '@/test/utils'
import { GROUPS, overviewFixture } from '@/test/reports-fixtures'
import type { RecurringRow } from '@/lib/api'

const api = vi.hoisted(() => ({
  reports: { recurring: vi.fn(), overview: vi.fn() },
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

const rec = (key: string, label: string, category: string, month: number, status: RecurringRow['status'], last = '2026-09-05'): RecurringRow => ({
  merchant_key: key,
  label,
  category,
  amount_month: month.toFixed(2),
  amount_year: (month * 12).toFixed(2),
  status,
  last_date: last,
  months_seen: 12,
})

const ROWS = [
  rec('NETFLIX', 'Netflix', 'Assinaturas', 55.9, 'up', '2026-10-02'),
  rec('SON PET VIDA', 'Son Pet Vida', 'Pets', 786, 'steady'),
  rec('STELLANTIS', 'Stellantis', 'Financiamento', 4265, 'steady'),
  rec('GYM', 'Academia', 'Saúde', 120, 'stopped', '2026-05-10'),
  rec('PIX JOAO', 'Pix João', 'Sem categoria', 300, 'new'),
]

function Probe() {
  const location = useLocation()
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
}

beforeEach(() => {
  vi.clearAllMocks()
  api.categoryGroups.list.mockResolvedValue(GROUPS)
  api.reports.recurring.mockResolvedValue(ROWS)
  // R$ 13,800 a month of spending: the active fixed costs are 39% of it.
  api.reports.overview.mockResolvedValue(
    overviewFixture({ kpis: { spending: '13800.00', income: '20000.00', left: '6200.00', left_share: 0.31, fixed_share: 0.4 } }),
  )
})

const names = () => screen.getAllByTestId('recurring-row').map((r) => within(r).getAllByText(/./)[0].textContent)

describe('RecurringTab', () => {
  it('opens with the monthly and yearly cost of the fixed charges and their share of spending', async () => {
    renderWithProviders(<RecurringTab />, { route: '/reports?tab=recurring&period=last_12m' })

    await waitFor(() =>
      expect(screen.getByTestId('report-headline')).toHaveTextContent(
        'R$5,407/month in fixed costs = R$64.9K/year, 39% of spending.',
      ),
    )
    expect(screen.getByTestId('recurring-counts')).toHaveTextContent('4 active · 1 new · 1 went up · 1 stopped')
  })

  it('lists the most expensive per year first, stopped ones last, each with its status', async () => {
    renderWithProviders(<RecurringTab />)

    await screen.findAllByTestId('recurring-row')
    expect(names()).toEqual(['Stellantis', 'Son Pet Vida', 'Pix João', 'Netflix', 'Academia'])
    const first = screen.getAllByTestId('recurring-row')[0]
    expect(within(first).getByTestId('status-steady')).toHaveTextContent(t('reports.recurring.status.steady'))
    expect(screen.getByTestId('status-new')).toBeInTheDocument()
    expect(screen.getByTestId('status-up')).toBeInTheDocument()
    expect(screen.getByTestId('status-stopped')).toBeInTheDocument()
    expect(first).toHaveTextContent('R$51.2K/year')
  })

  it('sorts by the latest charge or by name, and filters by status', async () => {
    const { user } = renderWithProviders(<RecurringTab />)
    await screen.findAllByTestId('recurring-row')

    await user.click(screen.getByTestId('recurring-sort-recent'))
    expect(names()[0]).toBe('Netflix')
    await user.click(screen.getByTestId('recurring-sort-name'))
    expect(names()).toEqual(['Academia', 'Netflix', 'Pix João', 'Son Pet Vida', 'Stellantis'])

    await user.click(screen.getByTestId('recurring-filter-stopped'))
    expect(names()).toEqual(['Academia'])
  })

  it('a charge opens its store in Explore, under its category and group', async () => {
    const { user } = renderWithProviders(
      <>
        <RecurringTab />
        <Probe />
      </>,
      { route: '/reports?tab=recurring&period=last_6m' },
    )
    await screen.findAllByTestId('recurring-row')
    await waitFor(() => expect(api.categoryGroups.list).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: /^Son Pet Vida/ }))
    expect(screen.getByTestId('location')).toHaveTextContent(
      '/reports?tab=explore&period=last_6m&group=g-life&category=c-pets&merchant=SON+PET+VIDA',
    )
  })

  it('a charge without category opens the uncategorized store', async () => {
    const { user } = renderWithProviders(
      <>
        <RecurringTab />
        <Probe />
      </>,
    )
    await user.click(await screen.findByRole('button', { name: /^Pix João/ }))
    expect(screen.getByTestId('location')).toHaveTextContent('category=none&merchant=PIX+JOAO')
  })

  it('retries after an error', async () => {
    api.reports.recurring.mockRejectedValueOnce(new Error('boom'))
    const { user } = renderWithProviders(<RecurringTab />)
    await user.click(await screen.findByRole('button', { name: t('reports.common.retry') }))
    expect(await screen.findAllByTestId('recurring-row')).toHaveLength(5)
  })

  it('shows the empty state', async () => {
    api.reports.recurring.mockResolvedValue([])
    renderWithProviders(<RecurringTab />)
    expect(await screen.findByText(t('reports.recurring.empty'))).toBeInTheDocument()
  })
})
