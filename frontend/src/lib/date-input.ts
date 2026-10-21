/**
 * Typed-date helpers for the transactions "Between dates" filter: parse what a
 * person types (tolerant: 1/2/26, 01/02/2026, 2026-02-01) into an ISO
 * yyyy-mm-dd string following the locale's field order, and format ISO back
 * for the field and for the compact filter label.
 */
type Part = 'day' | 'month' | 'year'

function fieldOrder(locale: string): Part[] {
  const parts = new Intl.DateTimeFormat(locale, {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  }).formatToParts(new Date(2006, 10, 22))
  const order = parts
    .map((p) => p.type)
    .filter((type): type is Part => type === 'day' || type === 'month' || type === 'year')
  return order.length === 3 ? order : ['day', 'month', 'year']
}

function build(year: number, month: number, day: number): string | null {
  const d = new Date(year, month - 1, day)
  if (d.getFullYear() !== year || d.getMonth() !== month - 1 || d.getDate() !== day) return null
  const pad = (n: number, w: number) => String(n).padStart(w, '0')
  return `${pad(year, 4)}-${pad(month, 2)}-${pad(day, 2)}`
}

/** 2-digit years: up to 10 years past the current one is 20xx, otherwise 19xx. */
function expandTwoDigitYear(yy: number): number {
  const limit = (new Date().getFullYear() % 100) + 10
  return yy <= limit ? 2000 + yy : 1900 + yy
}

/** True when the text already has three groups and a 2- or 4-digit last one. */
export function looksCompleteDate(text: string): boolean {
  const parts = text.replace(/^\D+|\D+$/g, '').split(/\D+/)
  return parts.length === 3 && (parts[2].length === 2 || parts[2].length === 4 || parts[0].length === 4)
}

/** Returns an ISO date, or null when the text is not a complete valid date. */
export function parseDateInput(text: string, locale: string): string | null {
  // Some locales end with a separator (hu: "2026. 03. 25."); drop it before splitting.
  const parts = text.replace(/^\D+|\D+$/g, '').split(/\D+/)
  if (parts.length !== 3 || parts.some((p) => !/^\d+$/.test(p))) return null

  let order = fieldOrder(locale)
  // A leading four-digit group is always the year (ISO-style), whatever the locale.
  if (parts[0].length === 4) order = ['year', 'month', 'day']

  const value: Record<Part, number> = { day: 0, month: 0, year: 0 }
  for (let i = 0; i < 3; i++) {
    const raw = parts[i]
    if (order[i] === 'year') {
      if (raw.length !== 2 && raw.length !== 4) return null
      value.year = raw.length === 2 ? expandTwoDigitYear(Number(raw)) : Number(raw)
    } else {
      if (raw.length > 2) return null
      value[order[i]] = Number(raw)
    }
  }
  return build(value.year, value.month, value.day)
}

function formatWith(iso: string, locale: string, year: 'numeric' | '2-digit'): string {
  if (!iso) return ''
  return new Date(iso + 'T00:00:00').toLocaleDateString(locale, {
    day: '2-digit',
    month: '2-digit',
    year,
  })
}

/** Field text, e.g. 01/02/2026. */
export function formatDateInput(iso: string, locale: string): string {
  return formatWith(iso, locale, 'numeric')
}

/** Compact label text, e.g. 01/02/26. */
export function formatDateShort(iso: string, locale: string): string {
  return formatWith(iso, locale, '2-digit')
}
