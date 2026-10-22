import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { ExploreTab } from '@/components/reports/explore/explore-tab'
import { squarify } from '@/components/reports/explore/squarify'
import { renderWithProviders, t } from '@/test/utils'
import { GROUPS } from '@/test/reports-fixtures'
import type { BreakdownResponse, BreakdownRow } from '@/lib/api'

const api = vi.hoisted(() => ({ reports: { breakdown: vi.fn() }, categoryGroups: { list: vi.fn() } }))

vi.mock('@/lib/api', () => ({ reports: api.reports, categoryGroups: api.categoryGroups }))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

const row = (id: string, label: string, total: number): BreakdownRow => ({
  id, label, kind: 'group', total: total.toFixed(2), share: 0, previous_total: '0.00', change: null, count: 1, sparkline: [],
})

function response(rows: BreakdownRow[]): BreakdownResponse {
  return {
    period: { from: '2025-10-01', to: '2026-09-30', partial: false, previous_from: '2024-10-01', previous_to: '2025-09-30' },
    currency: 'BRL',
    total: rows.reduce((s, r) => s + Number(r.total), 0).toFixed(2),
    previous_total: null,
    rows,
    unconverted_count: 0,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.categoryGroups.list.mockResolvedValue(GROUPS)
})

describe('squarify', () => {
  it('fills the box with one tile per value, areas proportional', () => {
    const rects = squarify([60, 30, 10], { x: 0, y: 0, w: 100, h: 100 })
    const areas = rects.map((r) => Math.round(r.w * r.h))
    expect(areas).toEqual([6000, 3000, 1000])
    for (const r of rects) {
      expect(r.x + r.w).toBeLessThanOrEqual(100.001)
      expect(r.y + r.h).toBeLessThanOrEqual(100.001)
    }
  })
})

describe('Explore treemap', () => {
  it('draws up to 12 tiles plus "other", leaving the refunds out', async () => {
    const rows = [
      ...Array.from({ length: 15 }, (_, i) => row(`g${i}`, `Grupo ${i}`, 1500 - i * 50)),
      row('g-ref', 'Estornos', -300),
    ]
    api.reports.breakdown.mockResolvedValue(response(rows))
    renderWithProviders(<ExploreTab />, { route: '/reports?tab=explore&chart=treemap' })

    await waitFor(() => expect(screen.getAllByTestId('treemap-tile')).toHaveLength(12))
    const rest = screen.getByTestId('treemap-rest')
    expect(rest).toHaveAccessibleName(new RegExp(t('reports.explore.treemap.rest', { n: 3 }).replace(/[()]/g, '\\$&')))
    expect(screen.queryByRole('button', { name: /^Estornos/ })).not.toBeInTheDocument()
  })

  it('a tile goes down a level, like its row, and wears its group color', async () => {
    api.reports.breakdown.mockResolvedValue(response([row('g-car', 'Carro', 2000), row('g-home', 'Moradia', 1000)]))
    const { user } = renderWithProviders(<ExploreTab />, { route: '/reports?tab=explore&chart=treemap' })

    const tile = await screen.findByRole('button', { name: /^Carro: R\$2,000\.00 \(67%\)/ })
    await waitFor(() => expect(tile.style.borderTop).toContain('rgb(59, 130, 246)'))
    await user.click(tile)

    await waitFor(() =>
      expect(api.reports.breakdown).toHaveBeenLastCalledWith({ level: 'category', group_id: 'g-car', period: 'last_12m' }),
    )
  })
})
