/** Squarified treemap layout (Bruls, Huizing & van Wijk): tiles as close to
 *  squares as the values allow, biggest first, filling the box. */

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

function worst(row: number[], side: number, scale: number) {
  const sum = row.reduce((s, v) => s + v, 0) * scale
  if (sum === 0) return Infinity
  const max = Math.max(...row) * scale
  const min = Math.min(...row) * scale
  const s2 = side * side
  return Math.max((s2 * max) / (sum * sum), (sum * sum) / (s2 * min))
}

/** One rectangle per value, in the order given (sort them first). */
export function squarify(values: number[], box: Rect): Rect[] {
  const total = values.reduce((s, v) => s + Math.max(0, v), 0)
  if (total <= 0 || box.w <= 0 || box.h <= 0) return values.map(() => ({ x: box.x, y: box.y, w: 0, h: 0 }))
  const scale = (box.w * box.h) / total
  const out: Rect[] = []
  let rest = { ...box }
  let i = 0
  while (i < values.length) {
    const side = Math.min(rest.w, rest.h)
    const row: number[] = [Math.max(0, values[i])]
    let j = i + 1
    while (j < values.length) {
      const next = [...row, Math.max(0, values[j])]
      if (worst(next, side, scale) > worst(row, side, scale)) break
      row.push(Math.max(0, values[j]))
      j += 1
    }
    const area = row.reduce((s, v) => s + v, 0) * scale
    const horizontal = rest.w >= rest.h
    // Lay the row along the short side, then cut it off the remaining box.
    const thick = side > 0 ? area / side : 0
    let offset = 0
    for (const v of row) {
      const len = thick > 0 ? (v * scale) / thick : 0
      out.push(
        horizontal
          ? { x: rest.x, y: rest.y + offset, w: thick, h: len }
          : { x: rest.x + offset, y: rest.y, w: len, h: thick },
      )
      offset += len
    }
    rest = horizontal
      ? { x: rest.x + thick, y: rest.y, w: rest.w - thick, h: rest.h }
      : { x: rest.x, y: rest.y + thick, w: rest.w, h: rest.h - thick }
    i = j
  }
  return out
}
