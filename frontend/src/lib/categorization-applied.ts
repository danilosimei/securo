/** Grouping helpers for the "Applied" audit list. */
import type { AppliedEvent } from '@/types'

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
  runs: { runId: string; at: Date; eventIds: string[] }[]
}

/** A line's key: its event, or its row when it has no standing event. */
export function rowKey(event: AppliedEvent): string {
  return event.event_id ?? `tx-${event.transaction.id}`
}

/** Undo changes something: the line has an event and that event changed the
 *  category (a confirmation changed nothing; undoing it would only send the
 *  row back to Review with the same category). */
export function isUndoable(event: AppliedEvent): event is AppliedEvent & { event_id: string } {
  return event.event_id !== null && event.previous_category_id !== event.new_category_id
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
    const storeId = `${day}|${event.merchant_key}|${event.new_category_id ?? ''}`
    let group = stores.get(storeId)
    if (!group) {
      group = { key: storeId, name: event.store_name, categoryId: event.new_category_id, events: [] }
      stores.set(storeId, group)
      bucket.stores.push(group)
    }
    group.events.push(event)
    bucket.count += 1
    // A batch undo covers the lines an undo changes.
    if (event.run_id && isUndoable(event)) {
      const run = bucket.runs.find((r) => r.runId === event.run_id)
      if (run) run.eventIds.push(event.event_id)
      else bucket.runs.push({ runId: event.run_id, at, eventIds: [event.event_id] })
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
