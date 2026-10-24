import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { useLocation } from 'react-router-dom'

import { HabitsTab } from '@/components/reports/habits/habits-tab'
import { habitRanking, smallPurchases } from '@/components/reports/habits/habits-model'
import { renderWithProviders, t } from '@/test/utils'
import { GROUPS } from '@/test/reports-fixtures'
import type { HabitsResponse } from '@/lib/api'

const api = vi.hoisted(() => ({ reports: { habits: vi.fn() }, categoryGroups: { list: vi.fn() } }))

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

const WEEKDAYS = ['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom']

function fixture(overrides: Partial<HabitsResponse> = {}): HabitsResponse {
  return {
    period: { from: '2026-07-01', to: '2026-09-30', partial: false, previous_from: '2026-04-01', previous_to: '2026-06-30' },
    merchants: [
      { id: 'IFOOD', category_id: 'c-market', label: 'iFood', count: 60, per_month: 20, avg_ticket: '62.00', total: '3720.00', category: 'Mercado' },
      { id: 'SON PET VIDA', category_id: 'c-pets', label: 'Son Pet Vida', count: 3, per_month: 1, avg_ticket: '786.00', total: '2358.00', category: 'Bichos' },
      { id: 'PADARIA', category_id: 'c-market', label: 'Padaria', count: 45, per_month: 15, avg_ticket: '12.00', total: '540.00', category: 'Mercado' },
      { id: 'UBER', category_id: 'c-fuel', label: 'Uber', count: 24, per_month: 8, avg_ticket: '40.00', total: '960.00', category: 'Combustível' },
      { id: 'IPVA', category_id: null, label: 'IPVA', count: 1, per_month: 0.3, avg_ticket: '3100.00', total: '3100.00', category: 'Sem categoria' },
    ],
    weekdays: WEEKDAYS.map((label, weekday) => ({
      weekday,
      label,
      total: weekday === 5 ? '4100.00' : '1200.00',
      count: weekday === 5 ? 40 : 20,
    })),
    bands: [
      { label: 'até R$ 20', min: '0', max: '20', count: 798, total: '8120.00' },
      { label: 'R$ 20–50', min: '20', max: '50', count: 275, total: '5280.00' },
      { label: 'R$ 50–200', min: '50', max: '200', count: 120, total: '11000.00' },
      { label: 'R$ 200–1000', min: '200', max: '1000', count: 30, total: '14000.00' },
      { label: 'acima de R$ 1000', min: '1000', max: null, count: 5, total: '12000.00' },
    ],
    days: [
      { date: '2026-07-03', total: '120.00' },
      { date: '2026-08-15', total: '3100.00' },
      { date: '2026-09-05', total: '40.00' },
    ],
    ...overrides,
  }
}

function Probe() {
  const location = useLocation()
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
}

function renderTab(route = '/reports?tab=habits&period=last_3m') {
  return renderWithProviders(
    <>
      <HabitsTab />
      <Probe />
    </>,
    { route },
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  api.categoryGroups.list.mockResolvedValue(GROUPS)
  api.reports.habits.mockResolvedValue(fixture())
})

describe('habits model', () => {
  it('sums the purchases under R$ 50', () => {
    expect(smallPurchases(fixture().bands)).toEqual({ limit: 50, count: 1073, total: 13400 })
  })

  it('ranks where you buy most often, and where each purchase costs most among stores bought more than once', () => {
    const merchants = fixture().merchants
    expect(habitRanking(merchants, 'often').map((m) => m.label)).toEqual(['iFood', 'Padaria', 'Uber', 'Son Pet Vida', 'IPVA'])
    // IPVA (one purchase) is not a habit, however big.
    expect(habitRanking(merchants, 'pricey').map((m) => m.label)).toEqual(['Son Pet Vida', 'iFood', 'Uber', 'Padaria'])
  })
})

describe('HabitsTab', () => {
  it('opens with how many purchases and where you go back most, the name opening the store', async () => {
    renderTab()

    const headline = await screen.findByTestId('report-headline')
    expect(headline).toHaveTextContent(
      'You made 160 purchases in the period, about 53 a month. Where you go back most: iFood, 20 times a month.',
    )
    expect(within(headline).getByRole('link', { name: 'iFood' })).toHaveAttribute(
      'href',
      '/reports?tab=explore&period=last_3m&group=g-food&category=c-market&merchant=IFOOD',
    )
    expect(api.reports.habits).toHaveBeenCalledWith({ period: 'last_3m' })
  })

  it('two plain lists answer where you buy all the time and where each purchase costs most', async () => {
    const { user } = renderTab()

    const often = await screen.findByTestId('block-often')
    await waitFor(() => expect(often).toHaveTextContent(t('reports.habits.often.answer', { name: 'iFood', n: '20' })))
    const oftenRows = within(screen.getByTestId('habit-often')).getAllByRole('button')
    expect(oftenRows[0]).toHaveTextContent('iFood')
    expect(oftenRows[0]).toHaveTextContent('20 times a month')

    const pricey = screen.getByTestId('block-pricey')
    expect(pricey).toHaveTextContent(t('reports.habits.pricey.answer', { name: 'Son Pet Vida', value: 'R$786' }))
    await waitFor(() => expect(api.categoryGroups.list).toHaveBeenCalled())
    await user.click(within(screen.getByTestId('habit-pricey')).getByRole('button', { name: /^Son Pet Vida/ }))
    expect(screen.getByTestId('location')).toHaveTextContent(
      '/reports?tab=explore&period=last_3m&group=g-life&category=c-pets&merchant=SON+PET+VIDA',
    )
  })

  it('a store without category opens in Explore under "none"', async () => {
    const { user } = renderTab()

    await user.click(await within(await screen.findByTestId('habit-often')).findByRole('button', { name: /^IPVA/ }))
    expect(screen.getByTestId('location')).toHaveTextContent('/reports?tab=explore&period=last_3m&category=none&merchant=IPVA')
  })

  it('shows the 7 weekdays, Monday first, and names the heaviest', async () => {
    renderTab()

    const bars = await screen.findAllByTestId('weekday-bar')
    expect(bars).toHaveLength(7)
    expect(bars[0]).toHaveAccessibleName('Monday: R$1,200.00 in 20 purchases')
    const block = screen.getByTestId('block-weekdays')
    expect(block).toHaveTextContent(t('reports.habits.weekdays.question'))
    expect(block).toHaveTextContent('You spend the most on Saturday')
    expect(block).toHaveTextContent('R$4,100 in 40 purchases.')
  })

  it('shows the 5 value bands and what the small purchases add up to', async () => {
    renderTab()

    const bands = await screen.findAllByTestId('band-bar')
    expect(bands).toHaveLength(5)
    expect(bands[0]).toHaveTextContent('up to R$20')
    expect(bands[4]).toHaveTextContent('over R$1,000')
    const block = screen.getByTestId('block-bands')
    expect(block).toHaveTextContent(t('reports.habits.bands.answer', { band: 'up to R$20' }))
    expect(block).toHaveTextContent('1,073 purchases under R$50 add up to R$13.4K.')
  })

  it('says so when there are no purchases, and offers a retry on error', async () => {
    api.reports.habits.mockRejectedValueOnce(new Error('boom'))
    const { user } = renderTab()
    const retry = await within(await screen.findByTestId('block-often')).findByRole('button', { name: t('reports.common.retry') })

    api.reports.habits.mockResolvedValue(fixture({ merchants: [], weekdays: [], days: [] }))
    await user.click(retry)
    expect((await screen.findAllByText(t('reports.habits.empty'))).length).toBeGreaterThan(0)
  })
})
