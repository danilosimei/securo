import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import { useLocation } from 'react-router-dom'

import { MoneyMapView } from '@/components/reports/money-map/money-map'
import { buildMapModel } from '@/components/reports/money-map/money-map-model'
import { buildPalette } from '@/components/reports/common/group-colors'
import { renderWithProviders, t } from '@/test/utils'
import { GROUPS, overviewFixture } from '@/test/reports-fixtures'
import type { MoneyMap } from '@/lib/api'

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

const palette = buildPalette(GROUPS)
const MAP = overviewFixture().money_map

function Probe() {
  const location = useLocation()
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
}

function renderMap(map: MoneyMap = MAP, view: 'chart' | 'table' = 'chart') {
  return renderWithProviders(
    <>
      <MoneyMapView map={map} query={{ period: 'last_month' }} currency="BRL" locale="en-US" palette={palette} view={view} />
      <Probe />
    </>,
    { route: '/reports?tab=overview&period=last_month' },
  )
}

const nodeLabels = (kind: string) =>
  screen.queryAllByTestId(`map-node-${kind}`).map((n) => n.getAttribute('data-label'))

const setNarrow = (narrow: boolean) => {
  window.matchMedia = ((query: string) => ({
    matches: narrow && query.includes('max-width'),
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia
}

afterEach(() => setNarrow(false))

describe('buildMapModel', () => {
  it('folds groups under 2% of the spending into one, and opens them on request', () => {
    const folded = buildMapModel(MAP, false)
    expect(folded.groups.map((g) => g.label)).toEqual(['Moradia', 'Carro', 'Estilo de Vida'])
    expect(folded.folded).toEqual(expect.objectContaining({ value: 1000 }))
    expect(folded.folded?.groups.map((g) => g.label)).toEqual(['Presentes', 'Educação'])

    const open = buildMapModel(MAP, true)
    expect(open.folded).toBeNull()
    expect(open.groups).toHaveLength(5)
  })

  it('does not fold a single small group', () => {
    const map = { ...MAP, groups: MAP.groups.filter((g) => g.label !== 'Educação') }
    expect(buildMapModel(map, false).folded).toBeNull()
  })
})

describe('MoneyMapView', () => {
  it('draws income, every group above 2%, their categories and the missing money entering from the left', () => {
    renderMap()

    expect(nodeLabels('income')).toEqual(['Salário & Renda'])
    expect(nodeLabels('short')).toEqual([t('reports.moneyMap.short')])
    expect(nodeLabels('left')).toEqual([])
    expect(nodeLabels('hub')).toEqual([t('reports.moneyMap.hubSpending')])
    expect(nodeLabels('group')).toEqual(['Moradia', 'Carro', 'Estilo de Vida'])
    expect(nodeLabels('folded')).toEqual([t('reports.moneyMap.folded', { n: 2 })])
    expect(nodeLabels('category')).toEqual(['Aluguel', 'Energia', 'Financiamento', 'Combustível', 'Pets', 'Saúde'])
    expect(screen.getByTestId('money-map-refunds')).toHaveTextContent(
      t('reports.moneyMap.refunds', { value: 'R$7,142' }),
    )
    expect(screen.getByTestId('money-map-answer')).toHaveTextContent(
      'With R$36K in income you spent R$75.8K; Moradia takes the most: 53% of spending. You were R$39.8K short.',
    )
  })

  it('opens the folded groups on click', async () => {
    const { user } = renderMap()

    await user.click(screen.getAllByTestId('map-node-folded')[0])

    expect(nodeLabels('folded')).toEqual([])
    expect(nodeLabels('group')).toEqual(['Moradia', 'Carro', 'Estilo de Vida', 'Presentes', 'Educação'])
  })

  it('shows what was left when income beat spending', () => {
    renderMap({ ...MAP, income: [{ id: 'g-income', label: 'Salário & Renda', value: '80000.00' }], left: '4196.88' })

    expect(nodeLabels('left')).toEqual([t('reports.moneyMap.left')])
    expect(nodeLabels('short')).toEqual([])
    expect(nodeLabels('hub')).toEqual([t('reports.moneyMap.hubIncome')])
  })

  it('draws one income node per category, biggest first, and "A conferir" without id', () => {
    renderMap({
      ...MAP,
      income: [
        { id: 'c-salary', label: 'Salário', value: '30000.00' },
        { id: 'c-div', label: 'Dividendos', value: '5000.00' },
        { id: null, label: 'A conferir', value: '1000.00' },
      ],
      left: '0.00',
    })

    expect(nodeLabels('income')).toEqual(['Salário', 'Dividendos', 'A conferir'])
  })

  it('a group opens in Explore, and a category opens under its group', async () => {
    const { user } = renderMap()

    await user.click(screen.getAllByTestId('map-node-group')[1])
    expect(screen.getByTestId('location')).toHaveTextContent('/reports?tab=explore&period=last_month&group=g-car')

    const pets = screen.getAllByTestId('map-node-category').find((n) => n.getAttribute('data-label') === 'Pets')!
    pets.focus()
    await user.keyboard('{Enter}')
    expect(screen.getByTestId('location')).toHaveTextContent(
      '/reports?tab=explore&period=last_month&group=g-life&category=c-pets',
    )
  })

  it('on a phone, shows stacked bars and lists the groups on tap', async () => {
    setNarrow(true)
    const { user } = renderMap()

    const bars = screen.getByTestId('money-map-bars')
    expect(screen.queryByTestId('money-map-sankey')).not.toBeInTheDocument()
    expect(within(bars).getByText(t('reports.moneyMap.short'))).toBeInTheDocument()

    await user.click(within(bars).getByRole('button', { name: new RegExp(t('reports.moneyMap.spending')) }))
    await user.click(within(bars).getByRole('button', { name: /Carro/ }))
    expect(screen.getByTestId('location')).toHaveTextContent('group=g-car')
  })

  it('lists every row in the table view', () => {
    renderMap(MAP, 'table')

    const table = screen.getByTestId('money-map-table')
    for (const name of ['Salário & Renda', 'Moradia', 'Presentes', 'Educação', 'Pets', t('reports.moneyMap.short')]) {
      expect(within(table).getByText(name)).toBeInTheDocument()
    }
  })
})
