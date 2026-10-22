/** The money map's numbers, ready to draw: income sources, spending by group
 *  (the small ones folded into "other groups" until opened), the categories
 *  inside each group and what was left or missing. Pure, so it is tested
 *  apart from the drawing. */
import type { MoneyMap } from '@/lib/api'
import { formatPercent } from '@/components/reports/common/chart-utils'

/** A group under this share of the spending folds into "other groups". */
export const SMALL_GROUP_SHARE = 0.02

export interface MapItem {
  id: string | null
  label: string
  value: number
}

export interface MapGroup extends MapItem {
  categories: MapItem[]
}

export interface MapModel {
  income: MapItem[]
  groups: MapGroup[]
  /** Groups folded away, null when nothing is folded (or they are open). */
  folded: { value: number; groups: MapGroup[] } | null
  totalIncome: number
  totalSpending: number
  /** Positive: left over. Negative: missing. */
  left: number
  refunds: number
}

const positive = (items: { id: string | null; label: string; value: string }[]) =>
  items.map((i) => ({ id: i.id, label: i.label, value: Number(i.value) })).filter((i) => i.value > 0)

export function buildMapModel(map: MoneyMap, expanded: boolean): MapModel {
  const income = positive(map.income).sort((a, b) => b.value - a.value)
  const allGroups: MapGroup[] = map.groups
    .map((g) => ({
      id: g.id,
      label: g.label,
      value: Number(g.value),
      categories: positive(g.categories).sort((a, b) => b.value - a.value),
    }))
    .filter((g) => g.value > 0)
    .sort((a, b) => b.value - a.value)
  const totalIncome = income.reduce((s, i) => s + i.value, 0)
  const totalSpending = allGroups.reduce((s, g) => s + g.value, 0)
  const small = allGroups.filter((g) => totalSpending > 0 && g.value / totalSpending < SMALL_GROUP_SHARE)
  // Folding a single group hides it for nothing: fold only two or more.
  const fold = !expanded && small.length >= 2
  return {
    income,
    groups: fold ? allGroups.filter((g) => !small.includes(g)) : allGroups,
    folded: fold ? { value: small.reduce((s, g) => s + g.value, 0), groups: small } : null,
    totalIncome,
    totalSpending,
    left: Number(map.left),
    refunds: Number(map.refunds),
  }
}

/** Whether there are small groups that can be folded at all. */
export function hasFoldableGroups(map: MoneyMap) {
  return buildMapModel(map, false).folded !== null
}

/** One sentence that answers "where did the money go?". */
export function moneyMapAnswer(model: MapModel, t: (k: string, o?: Record<string, unknown>) => string, money: (v: number) => string, locale: string) {
  const top = model.groups[0]
  if (!top) return ''
  const base = model.totalSpending
  const first = t(model.totalIncome > 0 ? 'reports.moneyMap.answerIncome' : 'reports.moneyMap.answerSpending', {
    income: money(model.totalIncome),
    spending: money(model.totalSpending),
    group: top.label,
    share: formatPercent(base > 0 ? top.value / base : 0, locale),
  })
  const tail =
    model.left > 0
      ? t('reports.moneyMap.answerLeft', { left: money(model.left) })
      : model.left < 0
        ? t('reports.moneyMap.answerShort', { left: money(-model.left) })
        : ''
  return tail ? `${first} ${tail}` : first
}
