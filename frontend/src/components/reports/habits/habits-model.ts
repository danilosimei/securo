/** Pure helpers of the Habits tab. */
import type { HabitsBand, HabitsMerchant, HabitsResponse } from '@/lib/api'
import { formatMoneyShort } from '@/components/reports/common/chart-utils'

/** Weekday name (0 = Monday) in the display locale. 1 Jan 2024 was a Monday. */
export function weekdayName(weekday: number, locale: string, style: 'short' | 'long' = 'short') {
  return new Date(2024, 0, 1 + weekday).toLocaleDateString(locale, { weekday: style }).replace('.', '')
}

const median = (values: number[]) => {
  const sorted = [...values].sort((a, b) => a - b)
  if (sorted.length === 0) return 0
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

export type Quadrant = 'costlyHabit' | 'bigRare' | 'small' | 'other'

/** Splits the stores at the median frequency and median ticket. */
export function quadrants(merchants: HabitsMerchant[]) {
  const freq = median(merchants.map((m) => m.per_month))
  const ticket = median(merchants.map((m) => Number(m.avg_ticket)))
  const of = (m: HabitsMerchant): Quadrant => {
    const often = m.per_month >= freq
    const pricey = Number(m.avg_ticket) >= ticket
    if (often && pricey) return 'costlyHabit'
    if (!often && pricey) return 'bigRare'
    if (often && !pricey) return 'small'
    return 'other'
  }
  return { freq, ticket, of }
}

/** The store that weighs most among the frequent and pricey ones. */
export function costliestHabit(merchants: HabitsMerchant[]) {
  if (merchants.length === 0) return null
  const { of } = quadrants(merchants)
  const byTotal = [...merchants].sort((a, b) => Number(b.total) - Number(a.total))
  return byTotal.find((m) => of(m) === 'costlyHabit') ?? byTotal[0]
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

/** Day cells for a heat calendar from `from` to `to`, in Monday-first weeks. */
export function calendarWeeks(from: string, to: string, days: { date: string; total: string }[]) {
  const values = new Map(days.map((d) => [d.date, Number(d.total)]))
  const start = new Date(`${from}T00:00:00`)
  const end = new Date(`${to}T00:00:00`)
  const iso = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  // Back up to the Monday of the first week.
  const cursor = new Date(start)
  cursor.setDate(cursor.getDate() - ((cursor.getDay() + 6) % 7))
  const weeks: ({ date: string; value: number } | null)[][] = []
  while (cursor <= end) {
    const week: ({ date: string; value: number } | null)[] = []
    for (let i = 0; i < 7; i++) {
      const inside = cursor >= start && cursor <= end
      week.push(inside ? { date: iso(cursor), value: values.get(iso(cursor)) ?? 0 } : null)
      cursor.setDate(cursor.getDate() + 1)
    }
    weeks.push(week)
  }
  return weeks
}

/** Upper bounds of 4 color steps over the days with spending (quartiles). */
export function heatSteps(values: number[]) {
  const spend = values.filter((v) => v > 0).sort((a, b) => a - b)
  if (spend.length === 0) return [0, 0, 0, 0]
  const q = (p: number) => spend[Math.min(spend.length - 1, Math.floor(p * spend.length))]
  return [q(0.25), q(0.5), q(0.75), spend[spend.length - 1]]
}

/** "até R$ 20", "R$ 20–50", "acima de R$ 1.000", from the band's bounds. */
export function bandLabel(band: HabitsBand, currency: string, locale: string, t: (k: string, o?: Record<string, unknown>) => string) {
  const money = (v: string) => formatMoneyShort(Number(v), currency, locale)
  if (Number(band.min) <= 0 && band.max !== null) return t('reports.habits.bands.upTo', { max: money(band.max) })
  if (band.max === null) return t('reports.habits.bands.above', { min: money(band.min) })
  return t('reports.habits.bands.between', { min: money(band.min), max: money(band.max) })
}
