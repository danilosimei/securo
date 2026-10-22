/** One color per category group across every Reports chart. The color is the
 *  group's own (`category_groups.color`, the one the Categories page shows),
 *  so a group looks the same here as everywhere else in the app; categories
 *  take shades of their group's color.
 *
 *  Groups are user data, so two of them can share a color (the default seed
 *  repeats a few). Those collisions are settled once, in group order, by
 *  handing the later group the first slot of a colorblind-checked fallback
 *  palette that sits far enough from every color already taken. Color follows
 *  the group, never its rank in a chart, so filtering never repaints. */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { categoryGroups as categoryGroupsApi } from '@/lib/api'
import type { CategoryGroup } from '@/types'

/** Neutral for folded rows ("Demais", no group): reads as "the rest". */
export const NEUTRAL_COLOR = 'var(--muted-foreground)'
/** Money left over / money missing. Status colors, always shown with a label. */
export const LEFT_COLOR = 'var(--chart-3)'
export const SHORT_COLOR = 'var(--destructive)'
/** The hub of the money map: all the money, before it splits. */
export const HUB_COLOR = 'color-mix(in oklab, var(--foreground) 55%, var(--card))'

// Validated categorical palette (adjacent CVD ΔE ≥ 8 in light and dark),
// only used to break a collision between two groups with the same color.
const FALLBACK = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948']

// Below this OKLab distance (×100) two colors read as the same series.
const MIN_DISTANCE = 10

type Lab = [number, number, number]

function hexToLab(hex: string): Lab | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return null
  const n = parseInt(m[1], 16)
  const lin = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => {
    const v = c / 255
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
  })
  const [r, g, b] = lin
  const l = Math.cbrt(0.4122214708 * r + 0.5363015174 * g + 0.0514770142 * b)
  const mm = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  return [
    0.2104542553 * l + 0.793617785 * mm - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * mm + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * mm - 0.808675766 * s,
  ]
}

/** OKLab distance ×100 between two hex colors (Infinity if either is invalid). */
export function colorDistance(a: string, b: string): number {
  const la = hexToLab(a)
  const lb = hexToLab(b)
  if (!la || !lb) return Infinity
  return Math.hypot(la[0] - lb[0], la[1] - lb[1], la[2] - lb[2]) * 100
}

/** Group id -> its chart color, collisions settled in group order. */
export function assignGroupColors(groups: Pick<CategoryGroup, 'id' | 'color' | 'position'>[]): Map<string, string> {
  const out = new Map<string, string>()
  const taken: string[] = []
  const ordered = [...groups].sort((a, b) => a.position - b.position)
  for (const group of ordered) {
    const own = hexToLab(group.color ?? '') ? group.color : null
    const clashes = (hex: string) => taken.some((t) => colorDistance(hex, t) < MIN_DISTANCE)
    let color = own && !clashes(own) ? own : null
    if (!color) color = FALLBACK.find((hex) => !clashes(hex)) ?? own ?? FALLBACK[taken.length % FALLBACK.length]
    out.set(group.id, color)
    taken.push(color)
  }
  return out
}

/** Shade `step` of a group color: 0 is the color itself, each step mixes a
 *  little more of the card surface in, so it works in light and dark. */
export function shade(color: string, step: number): string {
  if (step <= 0) return color
  const keep = Math.max(45, 100 - (step % 4) * 17)
  return `color-mix(in oklab, ${color} ${keep}%, var(--card))`
}

/** A soft tint of a color over the card, for large areas (treemap tiles). */
export function tint(color: string, amount = 22): string {
  return `color-mix(in oklab, ${color} ${amount}%, var(--card))`
}

export interface GroupPalette {
  /** Color of a group by id, falling back to its name. */
  group: (id: string | null | undefined, label?: string) => string
  /** Color of a category (a shade of its group's), by id or name. */
  category: (id: string | null | undefined, label?: string) => string
  /** Group id of a category id. */
  groupOfCategory: (id: string) => string | null
  /** Names by id, for Explore's breadcrumb. */
  groupName: (id: string) => string | undefined
  categoryName: (id: string) => string | undefined
}

export function buildPalette(groups: CategoryGroup[]): GroupPalette {
  const colors = assignGroupColors(groups)
  const groupByName = new Map<string, string>()
  const categories = new Map<string, { groupId: string; index: number; name: string }>()
  const groupNames = new Map<string, string>()
  const categoryNames = new Map<string, { id: string; groupId: string; groupName: string }>()
  for (const group of groups) {
    groupByName.set(group.name.toLowerCase(), group.id)
    groupNames.set(group.id, group.name)
    group.categories?.forEach((category, index) => {
      categories.set(category.id, { groupId: group.id, index, name: category.name })
      const key = category.name.toLowerCase()
      if (!categoryNames.has(key)) categoryNames.set(key, { id: category.id, groupId: group.id, groupName: group.name })
    })
  }
  const groupColor = (id: string | null | undefined, label?: string) => {
    const gid = (id && colors.has(id) ? id : null) ?? (label ? groupByName.get(label.toLowerCase()) : undefined)
    return (gid && colors.get(gid)) || NEUTRAL_COLOR
  }
  return {
    group: groupColor,
    category: (id, label) => {
      const known = (id ? categories.get(id) : undefined) ??
        (label ? (() => {
          const hit = categoryNames.get(label.toLowerCase())
          return hit ? categories.get(hit.id) : undefined
        })() : undefined)
      if (!known) return NEUTRAL_COLOR
      return shade(groupColor(known.groupId), known.index)
    },
    groupOfCategory: (id) => categories.get(id)?.groupId ?? null,
    groupName: (id) => groupNames.get(id),
    categoryName: (id) => categories.get(id)?.name,
  }
}

/** The palette of the current workspace's groups (shared query cache). */
export function useGroupPalette(): GroupPalette {
  const { data } = useQuery({
    queryKey: ['categoryGroups'],
    queryFn: categoryGroupsApi.list,
    staleTime: 5 * 60 * 1000,
  })
  return useMemo(() => buildPalette(data ?? []), [data])
}
