import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { useLocation } from 'react-router-dom'

import { OverviewTab } from '@/components/reports/overview/overview-tab'
import { renderWithProviders, t } from '@/test/utils'
import { GROUPS, overviewFixture, previousOverview } from '@/test/reports-fixtures'
import type { ReportPeriodParams } from '@/lib/api'

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

function Probe() {
  const location = useLocation()
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
}

function renderTab(route = '/reports?tab=overview&period=last_month') {
  return renderWithProviders(
    <>
      <OverviewTab />
      <Probe />
    </>,
    { route },
  )
}

const where = () => screen.getByTestId('location').textContent ?? ''

beforeEach(() => {
  vi.clearAllMocks()
  api.categoryGroups.list.mockResolvedValue(GROUPS)
  api.reports.overview.mockImplementation((params: ReportPeriodParams) =>
    Promise.resolve(params.period === 'custom' ? previousOverview() : overviewFixture()),
  )
})

describe('OverviewTab', () => {
  it('opens with the answer: what was spent, against the month before, and what was missing', async () => {
    renderTab()

    await waitFor(() =>
      expect(screen.getByTestId('report-headline')).toHaveTextContent(
        'In September you spent R$75.8K, 86% more than in August, and you were R$39.8K short.',
      ),
    )
    expect(api.reports.overview).toHaveBeenCalledWith({ period: 'last_month' })
    // The previous period is asked for with its real dates.
    expect(api.reports.overview).toHaveBeenCalledWith({ period: 'custom', from: '2026-08-01', to: '2026-08-31' })
  })

  it('shows the four numbers with their change against the previous period', async () => {
    renderTab()

    const spending = await screen.findByTestId('kpi-spending')
    expect(spending).toHaveTextContent('R$75,803.12')
    await waitFor(() => expect(spending).toHaveTextContent(t('reports.overview.kpi.vsPrevious', { change: '+86%' })))
    expect(screen.getByTestId('kpi-left')).toHaveTextContent(t('reports.overview.kpi.short'))
    expect(screen.getByTestId('kpi-left')).toHaveTextContent('R$39,796.55')
    expect(screen.getByTestId('kpi-fixed')).toHaveTextContent(t('reports.overview.kpi.fixedValue', { share: '38%' }))
    expect(screen.getByTestId('kpi-fixed')).toHaveAttribute('href', '/reports?tab=recurring&period=last_month')
  })

  it('turns each highlight into a card that leads to the right place', async () => {
    renderTab()

    const heavy = await screen.findByTestId('highlight-heavy_group')
    expect(heavy).toHaveTextContent('R$54K')
    expect(heavy).toHaveTextContent('Carro custa R$ 54 mil por ano')
    expect(heavy).toHaveAttribute('href', '/reports?tab=explore&period=last_month&group=g-car')
    // The spike opens the month that spiked, as a custom period.
    expect(screen.getByTestId('highlight-spike')).toHaveAttribute(
      'href',
      '/reports?tab=explore&period=custom&from=2026-09-01&to=2026-09-30',
    )
    expect(screen.getByTestId('highlight-to_review')).toHaveAttribute('href', '/categorization')
  })

  it('lists what changed as diverging bars, and a click opens the group in Explore', async () => {
    const { user } = renderTab()

    const chart = await screen.findByTestId('changes-chart')
    const rows = within(chart).getAllByRole('button')
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('Estilo de Vida'),
      expect.stringContaining('Combustível'),
      expect.stringContaining('Alimentação'),
    ])
    expect(rows[2]).toHaveTextContent('−R$310')
    await user.click(rows[0])
    expect(where()).toBe('/reports?tab=explore&period=last_month&group=g-life')
  })

  it('a category in what changed opens it under its group', async () => {
    const { user } = renderTab()
    const chart = await screen.findByTestId('changes-chart')
    await waitFor(() => expect(api.categoryGroups.list).toHaveBeenCalled())
    await user.click(within(chart).getByRole('button', { name: /Combustível/ }))
    expect(where()).toBe('/reports?tab=explore&period=last_month&group=g-car&category=c-fuel')
  })

  it('marks the month above 1.5× the median and opens a month in Explore', async () => {
    const { user } = renderTab()

    await screen.findByTestId('month-2026-09')
    const block = screen.getByTestId('block-monthly')
    expect(block).toHaveTextContent(/September was the costliest month: R\$75\.8K, 2\.1× the R\$36K median/)
    await user.click(within(block).getByTestId('month-2026-09'))
    expect(where()).toBe('/reports?tab=explore&period=custom&from=2026-09-01&to=2026-09-30')
  })

  it('says it cannot load and retries', async () => {
    api.reports.overview.mockRejectedValueOnce(new Error('boom'))
    const { user } = renderTab()

    expect(await screen.findByText(t('reports.overview.error'))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('reports.common.retry') }))
    expect(await screen.findByTestId('report-headline')).toBeInTheDocument()
  })

  it('says so when the period has no spending and no income', async () => {
    api.reports.overview.mockResolvedValue(
      overviewFixture({ kpis: { spending: '0.00', income: '0.00', left: '0.00', left_share: null, fixed_share: null } }),
    )
    renderTab()

    expect(await screen.findByText(t('reports.overview.empty'))).toBeInTheDocument()
  })

  it('a period switch keeps the numbers up, dimmed, while the new ones load', async () => {
    const { user } = renderTab()
    await screen.findByTestId('kpi-spending')
    let release: (v: unknown) => void = () => {}
    api.reports.overview.mockImplementationOnce(() => new Promise((resolve) => { release = resolve }))

    await user.click(screen.getByRole('button', { name: t('reports.explore.period.last_3m') }))

    expect(screen.getByTestId('kpi-spending').closest('[aria-busy="true"]')).not.toBeNull()
    expect(where()).toBe('/reports?tab=overview&period=last_3m')
    release(overviewFixture())
  })
})
