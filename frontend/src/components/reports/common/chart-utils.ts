/** Small helpers shared by the Reports charts: sizes, formats, month names. */
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'

/** Width of an element, kept current. jsdom measures 0, so `fallback` stands in. */
export function useElementWidth<T extends HTMLElement>(fallback = 720) {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver((entries) => setWidth(entries[0]?.contentRect.width ?? 0))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return [ref, width > 0 ? width : fallback] as const
}

function subscribeMedia(query: string) {
  return (cb: () => void) => {
    if (typeof window === 'undefined' || !window.matchMedia) return () => {}
    const list = window.matchMedia(query)
    list.addEventListener?.('change', cb)
    return () => list.removeEventListener?.('change', cb)
  }
}

function useMedia(query: string) {
  return useSyncExternalStore(
    subscribeMedia(query),
    () => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query).matches : false),
    () => false,
  )
}

/** Phones: charts switch to their narrow form at 640px and below. */
export function useIsNarrow() {
  return useMedia('(max-width: 640px)')
}

export function usePrefersReducedMotion() {
  return useMedia('(prefers-reduced-motion: reduce)')
}

/** "R$ 75,8 mil" style money, for titles, labels and axes. */
export function formatMoneyShort(value: number, currency: string, locale: string) {
  const abs = Math.abs(value)
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    notation: abs >= 10000 ? 'compact' : 'standard',
    minimumFractionDigits: 0,
    maximumFractionDigits: abs >= 10000 ? 1 : 0,
  }).format(value)
}

/** "42%" (or "+42%" with `signed`). `value` is a ratio. */
export function formatPercent(value: number, locale: string, signed = false) {
  return new Intl.NumberFormat(locale, {
    style: 'percent',
    maximumFractionDigits: Math.abs(value) < 0.1 && value !== 0 ? 1 : 0,
    signDisplay: signed ? 'exceptZero' : 'auto',
  }).format(value)
}

/** "setembro" / "set/26" for a YYYY-MM or YYYY-MM-DD string. */
export function monthName(iso: string, locale: string, style: 'long' | 'short' = 'long') {
  const date = new Date(`${iso.slice(0, 7)}-01T00:00:00`)
  return style === 'long'
    ? date.toLocaleDateString(locale, { month: 'long' })
    : date.toLocaleDateString(locale, { month: 'short', year: '2-digit' })
}

/** The YYYY-MM months from `from` to `to`, oldest first. */
export function monthsBetween(from: string, to: string) {
  const out: string[] = []
  let [y, m] = from.slice(0, 7).split('-').map(Number)
  const [ey, em] = to.slice(0, 7).split('-').map(Number)
  while (y < ey || (y === ey && m <= em)) {
    out.push(`${y}-${String(m).padStart(2, '0')}`)
    m += 1
    if (m > 12) {
      m = 1
      y += 1
    }
  }
  return out
}

/** The 12 months a breakdown sparkline covers: the last one is the period's. */
export function sparklineMonths(periodTo: string, count = 12) {
  const [y, m] = periodTo.slice(0, 7).split('-').map(Number)
  return Array.from({ length: count }, (_, i) => {
    const d = new Date(y, m - 1 - (count - 1 - i), 1)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  })
}

/** Clean axis ticks for 0..max. */
export function niceTicks(max: number, count = 4): number[] {
  if (!(max > 0)) return [0]
  const raw = max / count
  const pow = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((f) => f * pow).find((s) => s >= raw) ?? raw
  const ticks: number[] = []
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(v)
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step)
  return ticks
}

/** Money written inside a server sentence ("R$ 54 mil"), so privacy mode can
 *  hide it like every other value. */
const MONEY_IN_TEXT = /(?:R\$|US\$|\$|€|£)\s?-?[\d.,]+(?:\s?(?:mil|mi|bi|k|M))?/g

export function maskMoneyInText(text: string, privacy: boolean, mask: string) {
  return privacy ? text.replace(MONEY_IN_TEXT, mask) : text
}
