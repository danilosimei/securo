import type { KeyboardEvent } from 'react'

/** Classes of a row that opens something when clicked. */
export const CLICKABLE_ROW_CLASS =
  'cursor-pointer focus-visible:outline-none focus-visible:bg-muted focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40'

/**
 * Props that make a list row (a `<tr>` or a `<div>`) open its detail or its
 * edit form: a click, or Enter / Space with the row focused. Spread them on the
 * row and add `CLICKABLE_ROW_CLASS`. Buttons and menus inside the row keep
 * working: only a key pressed on the row itself counts. A `<div>` row becomes a
 * button; a table row (`inTable`) keeps its row role so the cells keep their
 * column headers for a screen reader, and still takes focus and Enter.
 * @example `<tr {...clickableRow(() => edit(item))} className={cn('...', CLICKABLE_ROW_CLASS)}>`
 */
export function clickableRow(onActivate?: () => void, { inTable = false }: { inTable?: boolean } = {}) {
  if (!onActivate) return {}
  return {
    role: inTable ? undefined : ('button' as const),
    tabIndex: 0,
    onClick: () => onActivate(),
    onKeyDown: (event: KeyboardEvent) => {
      if (event.target !== event.currentTarget) return
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        onActivate()
      }
    },
  }
}
