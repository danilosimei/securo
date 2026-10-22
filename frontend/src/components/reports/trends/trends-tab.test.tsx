import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { useLocation } from 'react-router-dom'

import { TrendsTab } from '@/components/reports/trends/trends-tab'
import { buildLayers, categoryTrends } from '@/components/reports/trends/trends-model'
import { renderWithProviders, t } from '@/test/utils'
import { GROUPS } from '@/test/reports-fixtures'
import type { BreakdownParams, BreakdownResponse, BreakdownRow } from '@/lib/api'

const api = vi.hoisted(() => ({ reports: { breakdown: vi.fn() }, categoryGroups: { list: vi.fn() } }))

vi.mock('@/lib/api', () => ({ reports: api.reports, categoryGroups: api.categoryGroups }))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

const flat = (before: number, after: number) => [...Array(9).fill(before), ...Array(3).fill(after)].map((v) => v.toFixed(2))

const row = (id: string, label: string, kind: 'group' | 'category', spark: string[], group_label?: string): BreakdownRow => ({
  id,
  label,
  kind,
  total: spark.reduce((s, v) => s + Number(v), 0).toFixed(2),
  share: 0,
  previous_total: '0.00',
  change: null,
  count: 1,
  sparkline: spark,
  ...(group_label ? { group_label } : {}),
})

const response = (rows: BreakdownRow[]): BreakdownResponse => ({
  period: { from: '2025-10-01', to: '2026-09-30', partial: false, previous_from: '2024-10-01', previous_to: '2025-09-30' },
  currency: 'BRL',
  total: '0.00',
  previous_total: null,
  rows,
  unconverted_count: 0,
})

const GROUP_ROWS = [
  row('g-home', 'Moradia', 'group', flat(4000, 4000)),
  row('g-life', 'Estilo de Vida', 'group', flat(1000, 1420)),
  row('g-car', 'Carro', 'group', flat(2000, 1800)),
  row('g-ref', 'Estornos', 'category', flat(-50, -50)),
]
const CATEGORY_ROWS = [
  row('c-rent', 'Aluguel', 'category', flat(4000, 4000), 'Moradia'),
  row('c-pets', 'Pets', 'category', flat(1000, 1420), 'Estilo de Vida'),
  row('c-fuel', 'Combustível', 'category', flat(2000, 1800), 'Carro'),
  // Tiny: +300% on almost nothing must not lead.
  row('c-books', 'Livros', 'category', flat(1, 4), 'Educação'),
]

function Probe() {
  const location = useLocation()
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
}

beforeEach(() => {
  vi.clearAllMocks()
  api.categoryGroups.list.mockResolvedValue(GROUPS)
  api.reports.breakdown.mockImplementation((params: BreakdownParams) =>
    Promise.resolve(response(params.level === 'group' ? GROUP_ROWS : CATEGORY_ROWS)),
  )
})

describe('trends model', () => {
  it('stacks spending groups biggest first and leaves refunds out', () => {
    expect(buildLayers(GROUP_ROWS, 'Other').map((l) => l.label)).toEqual(['Moradia', 'Carro', 'Estilo de Vida'])
  })

  it('folds groups past the seventh into one layer', () => {
    const many = Array.from({ length: 10 }, (_, i) => row(`g${i}`, `G${i}`, 'group', flat(100 - i, 100 - i)))
    const layers = buildLayers(many, 'Other')
    expect(layers).toHaveLength(8)
    expect(layers[7]).toEqual(expect.objectContaining({ label: 'Other', rest: true }))
  })

  it('ranks categories by their rise in the last 3 months, small ones last', () => {
    const trends = categoryTrends(CATEGORY_ROWS)
    expect(trends.map((tr) => tr.row.label)).toEqual(['Pets', 'Aluguel', 'Combustível', 'Livros'])
    expect(trends[0].change).toBeCloseTo(0.42)
    expect(trends[3].material).toBe(false)
  })
})

describe('TrendsTab', () => {
  it('names the category that grew most and asks for both breakdowns', async () => {
    renderWithProviders(<TrendsTab />, { route: '/reports?tab=trends&period=last_12m' })

    expect(await screen.findByTestId('report-headline')).toHaveTextContent(
      'Pets grew the most: +42% in the last 3 months, now R$1,420 a month.',
    )
    expect(api.reports.breakdown).toHaveBeenCalledWith({ level: 'group', period: 'last_12m' })
    expect(api.reports.breakdown).toHaveBeenCalledWith({ level: 'category', group_id: 'all', period: 'last_12m' })
  })

  it('turns a group off and on from the legend', async () => {
    const { user } = renderWithProviders(<TrendsTab />)

    const items = await screen.findAllByTestId('trends-legend-item')
    expect(items.map((i) => i.textContent)).toEqual(['Moradia', 'Carro', 'Estilo de Vida'])
    await user.click(items[1])
    expect(items[1]).toHaveAttribute('aria-pressed', 'false')
    await user.click(items[1])
    expect(items[1]).toHaveAttribute('aria-pressed', 'true')
  })

  it('a category card opens Explore on that category, under its group', async () => {
    const { user } = renderWithProviders(
      <>
        <TrendsTab />
        <Probe />
      </>,
      { route: '/reports?tab=trends&period=last_6m' },
    )

    const grid = await screen.findByTestId('trends-grid')
    await waitFor(() => expect(api.categoryGroups.list).toHaveBeenCalled())
    const cards = within(grid).getAllByTestId('trends-card')
    expect(cards[0]).toHaveTextContent('Pets')
    expect(cards[0]).toHaveTextContent('+42%')
    // The small category stays behind "see all".
    expect(within(grid).queryByText('Livros')).not.toBeInTheDocument()
    await user.click(cards[0])
    expect(screen.getByTestId('location')).toHaveTextContent(
      '/reports?tab=explore&period=last_6m&group=g-life&category=c-pets',
    )
  })

  it('offers a retry when a block fails', async () => {
    api.reports.breakdown.mockImplementation((params: BreakdownParams) =>
      params.level === 'group' ? Promise.reject(new Error('boom')) : Promise.resolve(response(CATEGORY_ROWS)),
    )
    renderWithProviders(<TrendsTab />)

    const block = await screen.findByTestId('block-trends-area')
    expect(await within(block).findByRole('alert')).toBeInTheDocument()
    expect(within(block).getByRole('button', { name: t('reports.common.retry') })).toBeInTheDocument()
  })
})
