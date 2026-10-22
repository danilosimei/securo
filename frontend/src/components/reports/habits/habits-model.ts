/** Pure helpers of the Habits tab. */
import type { HabitsBand, HabitsMerchant, HabitsResponse } from '@/lib/api'
import { formatMoneyShort } from '@/components/reports/common/chart-utils'

/** Weekday name (0 = Monday) in the display locale. 1 Jan 2024 was a Monday. */
export function weekdayName(weekday: number, locale: string, style: 'short' | 'long' = 'short') {
  return new Date(2024, 0, 1 + weekday).toLocaleDateString(locale, { weekday: style }).replace('.', '')
}

/** Purchases under `limit` (bands that end at or below it), summed. */
export function smallPurchases(bands: HabitsBand[], limit = 50) {
  const small = bands.filter((b) => b.max !== null && Number(b.max) <= limit)
  return {
    limit,
    count: small.reduce((s, b) => s + b.count, 0),
    total: small.reduce((s, b) => s + Number(b.total), 0),
  }
}

export function totals(data: HabitsResponse) {
  return {
    count: data.weekdays.reduce((s, w) => s + w.count, 0),
    total: data.weekdays.reduce((s, w) => s + Number(w.total), 0),
  }
}

/** "até R$ 20", "R$ 20–50", "acima de R$ 1.000", from the band's bounds. */
export function bandLabel(band: HabitsBand, currency: string, locale: string, t: (k: string, o?: Record<string, unknown>) => string) {
  const money = (v: string) => formatMoneyShort(Number(v), currency, locale)
  if (Number(band.min) <= 0 && band.max !== null) return t('reports.habits.bands.upTo', { max: money(band.max) })
  if (band.max === null) return t('reports.habits.bands.above', { min: money(band.min) })
  return t('reports.habits.bands.between', { min: money(band.min), max: money(band.max) })
}

/** Stores in each list of the Habits tab. */
export const HABIT_LIST_SIZE = 8

/** The stores of a list, in order: most often, or the priciest purchase
 *  among stores bought from more than once (a one-off is not a habit; with
 *  none of those, every store counts). */
export function habitRanking(merchants: HabitsMerchant[], measure: 'often' | 'pricey') {
  if (measure === 'often') return [...merchants].sort((a, b) => b.per_month - a.per_month || b.count - a.count).slice(0, HABIT_LIST_SIZE)
  const repeated = merchants.filter((m) => m.count > 1)
  return [...(repeated.length ? repeated : merchants)]
    .sort((a, b) => Number(b.avg_ticket) - Number(a.avg_ticket))
    .slice(0, HABIT_LIST_SIZE)
}
