import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import { ExploreTab } from '@/components/reports/explore/explore-tab'
import { renderWithProviders, t } from '@/test/utils'
import type { BreakdownResponse } from '@/lib/api'

const api = vi.hoisted(() => ({ reports: { breakdown: vi.fn() } }))

vi.mock('@/lib/api', () => ({ reports: api.reports }))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

const SPARK = Array.from({ length: 12 }, (_, i) => String(100 + i * 10))

function response(overrides: Partial<BreakdownResponse> = {}): BreakdownResponse {
  return {
    period: { from: '2025-10-01', to: '2026-09-30', partial: false, previous_from: '2024-10-01', previous_to: '2025-09-30' },
    currency: 'BRL',
    total: '1000.00',
    previous_total: '800.00',
    rows: [
      { id: 'g1', label: 'Compras', kind: 'group', total: '600.00', share: 0.6, previous_total: '500.00', change: 0.2, count: 40, sparkline: SPARK },
      { id: null, label: 'Sem grupo', kind: 'group', total: '500.00', share: 0.5, previous_total: '300.00', change: null, count: 7, sparkline: SPARK },
      { id: 'g3', label: 'Estornos', kind: 'group', total: '-100.00', share: -0.1, previous_total: '0.00', change: null, count: 3, sparkline: SPARK },
    ],
    unconverted_count: 0,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.reports.breakdown.mockResolvedValue(response())
})

describe('ExploreTab', () => {
  it('lists groups with total, share and change, and the header total with its variation', async () => {
    renderWithProviders(<ExploreTab />)

    const row = (await screen.findByText('Compras')).closest('tr') as HTMLElement
    expect(within(row).getByText('R$600.00')).toBeInTheDocument()
    expect(within(row).getByText('60%')).toBeInTheDocument()
    expect(within(row).getByText('+20%')).toBeInTheDocument()
    expect(within(row).getByTestId('explore-sparkline')).toBeInTheDocument()
    expect(screen.getByText('R$1,000.00')).toBeInTheDocument()
    expect(screen.getByText(t('reports.explore.vsPrevious', { change: '+25%' }))).toBeInTheDocument()
    expect(api.reports.breakdown).toHaveBeenCalledWith({ level: 'group', period: 'last_12m' })
    expect(screen.getByText(/Oct 25 – Sep 26, closed months/)).toBeInTheDocument()
  })

  it('shows a negative group with its sign and "offsets" instead of a share', async () => {
    renderWithProviders(<ExploreTab />)

    const row = (await screen.findByText('Estornos')).closest('tr') as HTMLElement
    expect(within(row).getByText('-R$100.00')).toBeInTheDocument()
    expect(within(row).getByText(t('reports.explore.offsets'))).toBeInTheDocument()
    expect(within(row).queryByText(/%/)).not.toBeInTheDocument()
  })

  it('does not show a variation when the previous period was zero', async () => {
    api.reports.breakdown.mockResolvedValue(response({ previous_total: '0.00' }))
    renderWithProviders(<ExploreTab />)

    await screen.findByText('Compras')
    expect(screen.queryByText(/vs previous/)).not.toBeInTheDocument()
  })

  it('keeps the old table dimmed and busy while a new period loads', async () => {
    const { user } = renderWithProviders(<ExploreTab />)
    await screen.findByText('Compras')
    expect(screen.getByTestId('explore-content')).toHaveAttribute('aria-busy', 'false')

    let resolve!: (value: BreakdownResponse) => void
    api.reports.breakdown.mockReturnValueOnce(new Promise<BreakdownResponse>((r) => { resolve = r }))
    await user.click(screen.getByRole('button', { name: t('reports.explore.period.last_6m') }))

    await waitFor(() =>
      expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'group', period: 'last_6m' }),
    )
    const content = screen.getByTestId('explore-content')
    expect(content).toHaveAttribute('aria-busy', 'true')
    expect(content).toHaveClass('opacity-60')
    expect(screen.getByText('Compras')).toBeInTheDocument()

    resolve(response({ total: '10.00' }))
    await waitFor(() => expect(screen.getByTestId('explore-content')).toHaveAttribute('aria-busy', 'false'))
    expect(screen.getByRole('button', { name: t('reports.explore.period.last_6m') })).toHaveAttribute('aria-pressed', 'true')
  })

  it('says the partial range is partial', async () => {
    api.reports.breakdown.mockResolvedValue(
      response({ period: { from: '2026-09-01', to: '2026-09-12', partial: true, previous_from: '2026-08-01', previous_to: '2026-08-12' } }),
    )
    renderWithProviders(<ExploreTab />, { route: '/reports?tab=explore&period=this_month' })

    expect(await screen.findByText(/Sep 26 – Sep 26, partial/)).toBeInTheDocument()
  })

  it('explains an empty period', async () => {
    api.reports.breakdown.mockResolvedValue(response({ rows: [], total: '0.00', previous_total: '0.00' }))
    renderWithProviders(<ExploreTab />)

    expect(await screen.findByText(t('reports.explore.empty', { from: 'Oct 25', to: 'Sep 26' }))).toBeInTheDocument()
  })

  it('offers a retry when loading fails', async () => {
    api.reports.breakdown.mockRejectedValueOnce(new Error('boom'))
    const { user } = renderWithProviders(<ExploreTab />)

    expect(await screen.findByText(t('reports.explore.error'))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('reports.explore.retry') }))

    expect(await screen.findByText('Compras')).toBeInTheDocument()
    expect(api.reports.breakdown).toHaveBeenCalledTimes(2)
  })

  it('warns about transactions left out for lack of an exchange rate', async () => {
    api.reports.breakdown.mockResolvedValue(response({ unconverted_count: 2 }))
    renderWithProviders(<ExploreTab />)

    expect(await screen.findByText(t('reports.explore.unconverted', { n: 2 }))).toBeInTheDocument()
  })

  it('reads the period and a custom range from the URL', async () => {
    renderWithProviders(<ExploreTab />, { route: '/reports?tab=explore&period=custom&from=2026-01-01&to=2026-03-31' })

    await screen.findByText('Compras')
    expect(api.reports.breakdown).toHaveBeenCalledWith({ level: 'group', period: 'custom', from: '2026-01-01', to: '2026-03-31' })
  })

  it('calls onSelectRow when a row is clicked', async () => {
    const onSelectRow = vi.fn()
    const { user, unmount } = renderWithProviders(<ExploreTab onSelectRow={onSelectRow} />)
    await user.click((await screen.findByText('Compras')).closest('tr') as HTMLElement)
    expect(onSelectRow).toHaveBeenCalledWith(expect.objectContaining({ id: 'g1' }))
    unmount()
  })
})
