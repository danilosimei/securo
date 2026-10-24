import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { useLocation } from 'react-router-dom'

import { OverviewTab } from '@/components/reports/overview/overview-tab'
import { renderWithProviders, t } from '@/test/utils'
import { pickPeriod } from '@/test/report-period-actions'
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

function Probe() {
  const location = useLocation()
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
}

function renderTab(route = '/reports?tab=overview&period=last_month') {
  return renderWithProviders(
    <WithPeriodBar>
      <OverviewTab />
      <Probe />
    </WithPeriodBar>,
    { route },
  )
}

const where = () => screen.getByTestId('location').textContent ?? ''

beforeEach(() => {
  vi.clearAllMocks()
  api.categoryGroups.list.mockResolvedValue(GROUPS)
  api.reports.overview.mockResolvedValue(overviewFixture())
})

describe('OverviewTab', () => {
  it('opens with the answer: what was spent, against the month before, and what was missing', async () => {
    renderTab()

    await waitFor(() =>
      expect(screen.getByTestId('report-headline')).toHaveTextContent(
        'In September you spent R$75.8K, ▲86% more than in August, and you were R$39.8K short.',
      ),
    )
    // The numbers stand out and the change is a pill, red: spending went up.
    expect(screen.getByTestId('delta-pill')).toHaveAttribute('data-tone', 'bad')
    expect(api.reports.overview).toHaveBeenCalledWith({ period: 'last_month' })
    // The comparison rides in the same response: one call, not two.
    expect(api.reports.overview).toHaveBeenCalledTimes(1)
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

  it('warns when rows in another currency had no rate, and stays quiet otherwise', async () => {
    api.reports.overview.mockResolvedValue(overviewFixture({ unconverted_count: 3 }))
    const { unmount } = renderTab()
    expect(await screen.findByTestId('overview-unconverted')).toHaveTextContent(
      t('reports.explore.unconverted', { n: 3 }),
    )
    unmount()

    api.reports.overview.mockResolvedValue(overviewFixture())
    renderTab()
    await screen.findByTestId('kpi-spending')
    expect(screen.queryByTestId('overview-unconverted')).not.toBeInTheDocument()
  })

  it('turns each highlight into a card: tone, number, mini chart, what pulled it and what to do', async () => {
    renderTab()

    const heavy = await screen.findByTestId('highlight-heavy_group')
    expect(heavy).toHaveAttribute('data-tone', 'info')
    expect(heavy).toHaveTextContent(t('reports.overview.highlights.tone.info'))
    expect(within(heavy).getByTestId('highlight-value')).toHaveTextContent('R$54K')
    expect(heavy).toHaveTextContent('Carro custa R$ 54 mil por ano')
    expect(within(heavy).getByTestId('mini-trend')).toBeInTheDocument()
    const drivers = within(heavy).getByTestId('highlight-drivers')
    expect(within(drivers).getByRole('link', { name: /Financiamento/ })).toHaveAttribute(
      'href',
      '/reports?tab=explore&period=last_month&group=g-car&category=c-loan',
    )
    expect(within(heavy).getByTestId('highlight-action-explore')).toHaveAttribute(
      'href',
      '/reports?tab=explore&period=last_month&group=g-car',
    )
    // The spike opens the month that spiked, as a custom period.
    const spike = screen.getByTestId('highlight-spike')
    expect(spike).toHaveAttribute('data-tone', 'alert')
    expect(within(spike).getByTestId('highlight-action-month')).toHaveAttribute(
      'href',
      '/reports?tab=explore&period=custom&from=2026-09-01&to=2026-09-30',
    )
    // A change is written with its sign.
    expect(within(spike).getByTestId('highlight-drivers')).toHaveTextContent('+R$20K')
    const review = screen.getByTestId('highlight-to_review')
    expect(within(review).getByTestId('highlight-action-review')).toHaveAttribute('href', '/categorization')
    expect(within(review).queryByTestId('mini-trend')).not.toBeInTheDocument()
  })

  it('the spike card follows the month the server names, not the priciest bar', async () => {
    const data = overviewFixture()
    const july = { tab: 'explore', period: 'custom', from: '2026-07-01', to: '2026-07-31' }
    data.highlights = data.highlights.map((h) =>
      h.rule === 'spike' ? { ...h, link: july, actions: [{ kind: 'month' as const, label: 'Ver Jul/26', link: july }] } : h,
    )
    api.reports.overview.mockResolvedValue(data)
    renderTab()

    const spike = await screen.findByTestId('highlight-spike')
    expect(within(spike).getByTestId('highlight-action-month')).toHaveAttribute(
      'href',
      '/reports?tab=explore&period=custom&from=2026-07-01&to=2026-07-31',
    )
  })

  it('shows how the month is going first, as a band with the pace bar', async () => {
    const data = overviewFixture()
    data.highlights = [
      {
        rule: 'month_pace',
        title: 'Em 21 dias você gastou R$ 31 mil, 18% acima do normal',
        value: '31000.00',
        compare: 'Nesse passo, o mês fecha em R$ 46 mil',
        link: { tab: 'explore', period: 'this_month' },
        tone: 'alert',
        sparkline: null,
        drivers: [
          { label: 'Normal até hoje', value: '26271.00', link: null },
          { label: 'Nesse passo, fecha em', value: '45762.00', link: null },
          { label: 'Um mês normal', value: '38782.00', link: null },
        ],
        actions: [{ kind: 'month', label: 'Ver o mês', link: { tab: 'explore', period: 'this_month' } }],
      },
      ...data.highlights,
    ]
    api.reports.overview.mockResolvedValue(data)
    renderTab()

    const pace = await screen.findByTestId('month-pace')
    expect(pace).toHaveAttribute('data-tone', 'alert')
    expect(pace).toHaveTextContent('Em 21 dias você gastou R$ 31 mil, 18% acima do normal')
    expect(pace).toHaveTextContent(t('reports.overview.pace.normal'))
    expect(pace).toHaveTextContent('R$26.3K')
    expect(pace).toHaveTextContent('R$45.8K')
    expect(within(pace).getByRole('img')).toHaveAccessibleName(
      t('reports.overview.pace.aria', { spent: 'R$31K', normal: 'R$26.3K', projection: 'R$45.8K' }),
    )
    expect(within(pace).getByTestId('month-pace-action')).toHaveAttribute('href', '/reports?tab=explore&period=this_month')
    // The band is not one of the cards.
    expect(within(screen.getByTestId('highlights')).queryByTestId('month-pace')).not.toBeInTheDocument()
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

  it('marks the month well above normal and opens a month in Explore', async () => {
    const { user } = renderTab()

    await screen.findByTestId('month-2026-09')
    const block = screen.getByTestId('block-monthly')
    expect(block).toHaveTextContent('September was the costliest month: R$75.8K, 2.1 times a normal month')
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

    await pickPeriod(user, t('reports.explore.period.last_3m'))

    expect(screen.getByTestId('kpi-spending').closest('[aria-busy="true"]')).not.toBeNull()
    expect(where()).toBe('/reports?tab=overview&period=last_3m')
    release(overviewFixture())
  })
})
