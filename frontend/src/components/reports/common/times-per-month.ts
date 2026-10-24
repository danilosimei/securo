import type { TFunction } from 'i18next'

/** How often something happens, as a phrase people read at a glance:
 *  "menos de 1 vez por mês", "1 vez por mês", "2,5 vezes por mês",
 *  "20 vezes por mês". Fractions only show up between 1 and 3. */
export function timesPerMonth(t: TFunction, perMonth: number, locale: string): string {
  if (perMonth < 1) return t('reports.habits.timesPerMonthLess')
  if (perMonth < 1.5) return t('reports.habits.timesPerMonth', { count: 1, n: (1).toLocaleString(locale) })
  const rounded = perMonth >= 3 ? Math.round(perMonth) : Math.round(perMonth * 10) / 10
  return t('reports.habits.timesPerMonth', { count: Math.round(perMonth), n: rounded.toLocaleString(locale, { maximumFractionDigits: 1 }) })
}
