import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'

import { WhereMoneyCard } from '@/components/reports/where-money/where-money'
import { buildSlices } from '@/components/reports/where-money/where-money-model'
import { renderWithProviders, t } from '@/test/utils'
import type { BreakdownParams, BreakdownResponse, BreakdownRow } from '@/lib/api'

const api = vi.hoisted(() => ({
  reports: { breakdown: vi.fn() },
  categoryGroups: { list: vi.fn().mockResolvedValue([]) },
}))

vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

const row = (over: Partial<BreakdownRow>): BreakdownRow => ({
  id: 'x', label: 'X', kind: 'group', total: '100.00', share: 0.1, previous_total: '0', change: null, count: 1,
  sparkline: [], ...over,
})
const PERIOD = { from: '2026-10-01', to: '2026-10-31', partial: false, previous_from: '2026-09-01', previous_to: '2026-09-30' }
const resp = (rows: BreakdownRow[], total: string): BreakdownResponse => ({
  period: PERIOD, currency: 'BRL', total, previous_total: null, rows, unconverted_count: 0,
})

const GROUPS = [
  row({ id: 'g-food', label: 'Alimentação', total: '600.00' }),
  row({ id: 'g-shop', label: 'Compras', total: '300.00' }),
  row({ id: null, label: 'Sem grupo', total: '100.00' }),
]
const FOOD = [
  row({ id: 'c-rest', kind: 'category', label: 'Restaurantes', total: '400.00' }),
  row({ id: 'c-market', kind: 'category', label: 'Mercado', total: '200.00' }),
]

describe('buildSlices', () => {
  it('keeps what took money, biggest first, with its share of that money', () => {
    const slices = buildSlices(
      [row({ id: 'a', total: '100' }), row({ id: 'refund', total: '-50' }), row({ id: 'b', total: '300' })],
      'Others',
    )
    expect(slices.map((s) => [s.key, s.value, s.share])).toEqual([
      ['b', 300, 0.75],
      ['a', 100, 0.25],
    ])
  })

  it('folds what is past the last slice into one "Others" slice', () => {
    const rows = Array.from({ length: 9 }, (_, i) => row({ id: `g${i}`, label: `G${i}`, total: String(90 - i * 10) }))
    const slices = buildSlices(rows, 'Others', 7)
    expect(slices).toHaveLength(7)
    expect(slices.slice(0, 6).map((s) => s.key)).toEqual(['g0', 'g1', 'g2', 'g3', 'g4', 'g5'])
    const rest = slices[6]
    expect(rest).toMatchObject({ key: 'rest', label: 'Others', value: 30 + 20 + 10, row: null })
  })

  it('does not fold when the rows fit', () => {
    const rows = Array.from({ length: 7 }, (_, i) => row({ id: `g${i}`, total: '10' }))
    expect(buildSlices(rows, 'Others', 7).map((s) => s.key)).not.toContain('rest')
  })
})

describe('WhereMoneyCard', () => {
  const scope = { period: 'custom' as const, from: '2026-10-01', to: '2026-10-31' }

  beforeEach(() => {
    vi.clearAllMocks()
    api.reports.breakdown.mockImplementation(async (p: BreakdownParams) =>
      p.level === 'category' ? resp(FOOD, '600.00') : resp(GROUPS, '1000.00'),
    )
  })

  it('shows the spending of the period by group, with the total in the middle', async () => {
    renderWithProviders(<WhereMoneyCard scope={scope} onCategory={vi.fn()} />)
    const list = await screen.findByRole('list', { name: t('reports.whereMoney.byGroup') })
    expect(within(list).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual([
      'Alimentação: R$600.00 (60%)',
      'Compras: R$300.00 (30%)',
      'Sem grupo: R$100.00 (10%)',
    ])
    expect(screen.getByTestId('where-money-total')).toHaveTextContent('R$1,000')
    expect(api.reports.breakdown).toHaveBeenCalledWith({ level: 'group', ...scope })
  })

  it('opens the categories of a group and goes back', async () => {
    renderWithProviders(<WhereMoneyCard scope={scope} onCategory={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /^Alimentação:/ }))

    const list = await screen.findByRole('list', { name: t('reports.whereMoney.inGroup', { group: 'Alimentação' }) })
    expect(within(list).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual([
      'Restaurantes: R$400.00 (67%)',
      'Mercado: R$200.00 (33%)',
    ])
    expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'category', group_id: 'g-food', ...scope })

    fireEvent.click(screen.getByRole('button', { name: t('reports.whereMoney.back') }))
    expect(await screen.findByRole('list', { name: t('reports.whereMoney.byGroup') })).toBeInTheDocument()
  })

  it('opens the "no group" row as the group "none"', async () => {
    renderWithProviders(<WhereMoneyCard scope={scope} onCategory={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /^Sem grupo:/ }))
    await waitFor(() =>
      expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'category', group_id: 'none', ...scope }),
    )
  })

  it('hands a clicked category, with its group, to the page', async () => {
    const onCategory = vi.fn()
    renderWithProviders(<WhereMoneyCard scope={scope} onCategory={onCategory} />)
    fireEvent.click(await screen.findByRole('button', { name: /^Alimentação:/ }))
    fireEvent.click(await screen.findByRole('button', { name: /^Restaurantes:/ }))
    expect(onCategory).toHaveBeenCalledWith(
      { id: 'c-rest', label: 'Restaurantes' },
      { id: 'g-food', label: 'Alimentação' },
    )
  })

  it('says so when nothing was spent', async () => {
    api.reports.breakdown.mockResolvedValue(resp([], '0'))
    renderWithProviders(<WhereMoneyCard scope={scope} onCategory={vi.fn()} />)
    expect(await screen.findByText(t('reports.whereMoney.empty'))).toBeInTheDocument()
  })
})
