import { describe, expect, it } from 'vitest'

import { assignGroupColors, buildPalette, colorDistance, NEUTRAL_COLOR } from '@/components/reports/common/group-colors'
import { exploreLink, monthExploreLink, tabLink } from '@/components/reports/common/report-url'
import { formatMoneyShort, maskMoneyInText, niceTicks, sparklineMonths } from '@/components/reports/common/chart-utils'
import { GROUPS } from '@/test/reports-fixtures'

describe('group colors', () => {
  it('keeps each group its own color', () => {
    const colors = assignGroupColors(GROUPS)
    expect(colors.get('g-home')).toBe('#8B5CF6')
    expect(colors.get('g-car')).toBe('#3B82F6')
  })

  it('gives a later group with the same color a distinct one, the same every time', () => {
    const colors = assignGroupColors(GROUPS)
    const gifts = colors.get('g-gifts')!
    expect(gifts).not.toBe('#8B5CF6')
    for (const [id, color] of colors) {
      if (id !== 'g-gifts') expect(colorDistance(gifts, color)).toBeGreaterThanOrEqual(10)
    }
    expect(assignGroupColors([...GROUPS].reverse()).get('g-gifts')).toBe(gifts)
  })

  it('derives category shades from the group and finds group of a category', () => {
    const palette = buildPalette(GROUPS)
    expect(palette.category('c-loan')).toBe('#3B82F6')
    expect(palette.category('c-fuel')).toContain('color-mix(in oklab, #3B82F6')
    expect(palette.category(null, 'Pets')).toBe('#EC4899')
    expect(palette.category('nope')).toBe(NEUTRAL_COLOR)
    expect(palette.group(null, 'carro')).toBe('#3B82F6')
    expect(palette.groupOfCategory('c-pets')).toBe('g-life')
  })
})

describe('report links', () => {
  it('builds Explore links at each level, keeping a custom period', () => {
    expect(exploreLink({ period: 'last_3m', group: 'g1' }).to).toBe('/reports?tab=explore&period=last_3m&group=g1')
    expect(exploreLink({ period: 'custom', from: '2026-01-01', to: '2026-03-31', group: 'g1', category: 'c1', merchant: 'AMAZON' }).to).toBe(
      '/reports?tab=explore&period=custom&from=2026-01-01&to=2026-03-31&group=g1&category=c1&merchant=AMAZON',
    )
  })

  it('sends a store without category to its transactions', () => {
    expect(exploreLink({ period: 'last_12m', merchant: 'SON PET' }, {}, { from: '2025-10-01', to: '2026-09-30' }).to).toBe(
      '/transactions?from=2025-10-01&to=2026-09-30&merchant_key=SON+PET&from_screen=reports',
    )
  })

  it('opens a whole month, another tab or a single day', () => {
    expect(monthExploreLink('2026-02').to).toBe('/reports?tab=explore&period=custom&from=2026-02-01&to=2026-02-28')
    expect(tabLink('recurring', { period: 'ytd' }).to).toBe('/reports?tab=recurring&period=ytd')
  })
})

describe('chart helpers', () => {
  it('formats short money, compact from ten thousand', () => {
    expect(formatMoneyShort(786, 'BRL', 'pt-BR')).toBe('R$ 786')
    expect(formatMoneyShort(75803.12, 'BRL', 'pt-BR')).toBe('R$ 75,8 mil')
  })

  it('names the 12 months a sparkline covers', () => {
    expect(sparklineMonths('2026-09-30')).toEqual([
      '2025-10', '2025-11', '2025-12', '2026-01', '2026-02', '2026-03',
      '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09',
    ])
  })

  it('picks clean ticks', () => {
    expect(niceTicks(75803)).toEqual([0, 20000, 40000, 60000, 80000])
  })

  it('hides money written in a server sentence in privacy mode', () => {
    expect(maskMoneyInText('Carro custa R$ 54 mil por ano', true, '•••')).toBe('Carro custa ••• por ano')
    expect(maskMoneyInText('Carro custa R$ 54 mil por ano', false, '•••')).toBe('Carro custa R$ 54 mil por ano')
  })
})
