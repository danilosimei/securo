/** Shared report fixtures for the Reports tests (September 2026 study numbers). */
import type { CategoryGroup } from '@/types'
import type { OverviewResponse } from '@/lib/api'

const group = (id: string, name: string, color: string, position: number, categories: [string, string][]): CategoryGroup => ({
  id,
  user_id: 'u',
  name,
  icon: 'tag',
  color,
  position,
  is_system: false,
  is_hidden: false,
  categories: categories.map(([cid, cname]) => ({
    id: cid,
    user_id: 'u',
    group_id: id,
    name: cname,
    icon: 'tag',
    color,
    is_system: false,
    is_hidden: false,
    treat_as_transfer: false,
    is_ignored: false,
  })),
})

export const GROUPS: CategoryGroup[] = [
  group('g-home', 'Moradia', '#8B5CF6', 0, [['c-rent', 'Aluguel'], ['c-energy', 'Energia']]),
  group('g-car', 'Carro', '#3B82F6', 1, [['c-loan', 'Financiamento'], ['c-fuel', 'Combustível']]),
  group('g-life', 'Estilo de Vida', '#EC4899', 2, [['c-pets', 'Pets'], ['c-health', 'Saúde']]),
  group('g-food', 'Alimentação', '#F59E0B', 3, [['c-market', 'Mercado']]),
  group('g-gifts', 'Presentes', '#8B5CF6', 4, [['c-gift', 'Presentes']]),
  group('g-edu', 'Educação', '#0EA5E9', 5, [['c-books', 'Livros']]),
  group('g-income', 'Salário & Renda', '#16A34A', 6, [['c-salary', 'Salário']]),
]

const months = ['2025-10', '2025-11', '2025-12', '2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09']

export function overviewFixture(overrides: Partial<OverviewResponse> = {}): OverviewResponse {
  return {
    period: { from: '2026-09-01', to: '2026-09-30', partial: false, previous_from: '2026-08-01', previous_to: '2026-08-31' },
    currency: 'BRL',
    kpis: { spending: '75803.12', income: '36006.57', left: '-39796.55', left_share: -1.105, fixed_share: 0.38 },
    changes: [
      { kind: 'group', id: 'g-life', label: 'Estilo de Vida', change: 0.42, delta: '520.00' },
      { kind: 'group', id: 'g-food', label: 'Alimentação', change: -0.12, delta: '-310.00' },
      { kind: 'category', id: 'c-fuel', label: 'Combustível', change: 0.08, delta: '90.00' },
    ],
    highlights: [
      {
        rule: 'heavy_group',
        title: 'Carro custa R$ 54 mil por ano',
        value: '54012.30',
        compare: '9% do gasto',
        link: { tab: 'explore', period: 'last_month', group_id: 'g-car' },
      },
      {
        rule: 'spike',
        title: 'Setembro foi 2,1× a mediana',
        value: '75803.12',
        compare: 'Impostos +R$ 20 mil',
        link: { tab: 'explore', period: 'custom', from: '2026-09-01', to: '2026-09-30' },
      },
      {
        rule: 'to_review',
        title: '3 itens para revisar',
        value: '3',
        compare: '3 sugestões na Revisar',
        link: { tab: 'explore', period: 'last_month' },
      },
    ],
    monthly: months.map((month, i) => ({
      month,
      spending: month === '2026-09' ? '75803.12' : String(34000 + i * 500),
      income: '36000.00',
      median_spending: '36000.00',
    })),
    money_map: {
      income: [{ id: 'g-income', label: 'Salário & Renda', value: '36006.57' }],
      groups: [
        { id: 'g-home', label: 'Moradia', value: '40000.00', categories: [{ id: 'c-rent', label: 'Aluguel', value: '30000.00' }, { id: 'c-energy', label: 'Energia', value: '10000.00' }] },
        { id: 'g-car', label: 'Carro', value: '20000.00', categories: [{ id: 'c-loan', label: 'Financiamento', value: '15000.00' }, { id: 'c-fuel', label: 'Combustível', value: '5000.00' }] },
        { id: 'g-life', label: 'Estilo de Vida', value: '14803.12', categories: [{ id: 'c-pets', label: 'Pets', value: '10000.00' }, { id: 'c-health', label: 'Saúde', value: '4803.12' }] },
        { id: 'g-gifts', label: 'Presentes', value: '600.00', categories: [{ id: 'c-gift', label: 'Presentes', value: '600.00' }] },
        { id: 'g-edu', label: 'Educação', value: '400.00', categories: [{ id: 'c-books', label: 'Livros', value: '400.00' }] },
      ],
      refunds: '7141.52',
      left: '-39796.55',
    },
    ...overrides,
  }
}

/** August, the previous period of the September fixture. */
export const previousOverview = (): OverviewResponse =>
  overviewFixture({
    period: { from: '2026-08-01', to: '2026-08-31', partial: false, previous_from: '2026-07-01', previous_to: '2026-07-31' },
    kpis: { spending: '40805.50', income: '36000.00', left: '-4805.50', left_share: -0.133, fixed_share: 0.5 },
  })
