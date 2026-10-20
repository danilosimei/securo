/** Grouping helpers for the "Applied" audit list. */
import type { AppliedEvent } from '@/types'

/** Openers that say how the money moved, not who it went to. */
const GENERIC_FIRST_WORDS = new Set(['pix', 'ted', 'doc', 'compra', 'pagamento', 'pagto', 'transferencia', 'debito', 'credito'])

function titleCase(text: string) {
  return text.replace(/(^|\s)\p{L}/gu, (m) => m.toUpperCase())
}

/** A store key and a readable name from a raw bank description:
 *  "IFOOD *PEDIDO 8812" and "IFOOD *PEDIDO 9001" land together. */
export function storeFromDescription(description: string): { key: string; name: string } {
  const words = description
    .normalize('NFD')
    .replace(/\p{M}/gu, '')
    .toLowerCase()
    .replace(/[^a-z\s]/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
  if (words.length === 0) return { key: description.trim().toLowerCase(), name: description.trim() }
  let take = GENERIC_FIRST_WORDS.has(words[0]) ? 3 : words[0].length < 3 ? 2 : 1
  take = Math.min(take, words.length)
  const picked = words.slice(0, take).join(' ')
  return { key: picked, name: titleCase(picked) }
}

export interface StoreBucket {
  key: string
  name: string
  categoryId: string | null
  events: AppliedEvent[]
}

export interface DayBucket {
  day: string
  /** Noon of that day, for formatting. */
  date: Date
  stores: StoreBucket[]
  count: number
  runs: { runId: string; at: Date }[]
}

function dayKey(d: Date) {
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

/** Day (when it was applied, local time) first, then store + category. */
export function groupApplied(events: AppliedEvent[]): DayBucket[] {
  const days = new Map<string, DayBucket>()
  const stores = new Map<string, StoreBucket>()
  for (const event of events) {
    const at = new Date(event.created_at)
    const day = dayKey(at)
    let bucket = days.get(day)
    if (!bucket) {
      bucket = { day, date: new Date(at.getFullYear(), at.getMonth(), at.getDate(), 12), stores: [], count: 0, runs: [] }
      days.set(day, bucket)
    }
    const store = storeFromDescription(event.transaction.description)
    const storeId = `${day}|${store.key}|${event.new_category_id ?? ''}`
    let group = stores.get(storeId)
    if (!group) {
      group = { key: storeId, name: store.name, categoryId: event.new_category_id, events: [] }
      stores.set(storeId, group)
      bucket.stores.push(group)
    }
    group.events.push(event)
    bucket.count += 1
    if (event.run_id && !bucket.runs.some((r) => r.runId === event.run_id)) {
      bucket.runs.push({ runId: event.run_id, at })
    }
  }
  return [...days.values()]
}

export function isToday(d: Date, now = new Date()) {
  return dayKey(d) === dayKey(now)
}

export function isYesterday(d: Date, now = new Date()) {
  return dayKey(d) === dayKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1, 12))
}
