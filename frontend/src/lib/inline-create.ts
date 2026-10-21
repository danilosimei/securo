import { normalizeText } from '@/lib/utils'
import type { Category, CategoryGroup } from '@/types'

/**
 * The name a searchable picker should offer to create, or null when it
 * should not offer anything: the search is blank, or an existing option
 * already carries that name (ignoring case and accents), so creating it
 * would only make a duplicate.
 */
export function inlineCreateName(search: string, existingNames: string[]): string | null {
  const name = search.trim()
  if (!name) return null
  const wanted = normalizeText(name)
  return existingNames.some((existing) => normalizeText(existing.trim()) === wanted) ? null : name
}

/** cmdk value for the "Create" row. It carries the search text so the
 *  picker's own filter always keeps the row visible. */
export function inlineCreateItemValue(name: string): string {
  return `__create__ ${name}`
}

/** A category to create from a picker, in a group that may not exist yet. */
export interface GroupedCreateDraft {
  group: string
  name: string
  groupExists: boolean
}

export type GroupedCreateOption =
  | { kind: 'inGroup'; draft: GroupedCreateDraft }
  | { kind: 'pickGroup'; name: string }
  | { kind: 'newGroup'; draft: GroupedCreateDraft }

/** The server's ceiling for a group or category name, in characters. */
export const CREATE_NAME_MAX = 100

/** A name as the server stores it: trimmed, inner spaces squeezed. */
export function cleanCreateName(name: string): string {
  return name.trim().replace(/\s+/g, ' ')
}

const key = (name: string) => normalizeText(cleanCreateName(name))
const same = (a: string, b: string) => key(a) === key(b)
// Code points, as the server counts them (an emoji is one, not two).
const tooLong = (name: string) => [...name].length > CREATE_NAME_MAX

/** The search cut into `[group, name]` at its first slash, or null without one. */
function splitAtSlash(text: string): [string, string] | null {
  const slash = text.indexOf('/')
  if (slash < 0) return null
  return [cleanCreateName(text.slice(0, slash)), cleanCreateName(text.slice(slash + 1))]
}

/**
 * Whether the search names a group or a category longer than the server
 * takes: nothing is offered for it, and the picker says why instead.
 */
export function createNameTooLong(search: string): boolean {
  const text = cleanCreateName(search)
  const parts = splitAtSlash(text)
  return parts ? parts.some(tooLong) : tooLong(text)
}

/**
 * What a picker offers to create from its search text, Notion style.
 *
 * `Group / Name` (cut at the first slash) creates straight in that group,
 * spelled as the existing group when one matches, or offers nothing when
 * the group already has the name. A plain name offers, in order: the
 * suggested group, picking another group, and a new group of the same name
 * (it is born with that one category). Nothing is offered for a name some
 * category already carries (picking it is the answer), nor for a name over
 * `CREATE_NAME_MAX`. Names compare as the server does: no case, no accents,
 * spaces squeezed. Hidden groups count as absent, as on the server: a
 * category made in one would be hidden too, so it creates a visible one.
 */
export function groupedCreateOptions(
  search: string,
  categories: Category[],
  groups: CategoryGroup[],
  suggestedGroupId: string | null,
): GroupedCreateOption[] {
  const text = cleanCreateName(search)
  if (!text || createNameTooLong(text)) return []
  const visible = groups.filter((g) => !g.is_hidden)

  const parts = splitAtSlash(text)
  if (parts) {
    const [groupName, name] = parts
    if (!groupName || !name) return []
    const existing = visible.find((g) => same(g.name, groupName))
    if (existing && categories.some((c) => c.group_id === existing.id && same(c.name, name))) return []
    return [
      {
        kind: 'inGroup',
        draft: { group: existing?.name ?? groupName, name, groupExists: !!existing },
      },
    ]
  }

  if (categories.some((c) => same(c.name, text))) return []
  const options: GroupedCreateOption[] = []
  const suggested = suggestedGroupId ? visible.find((g) => g.id === suggestedGroupId) : undefined
  if (suggested) {
    options.push({ kind: 'inGroup', draft: { group: suggested.name, name: text, groupExists: true } })
  }
  if (visible.some((g) => g.id !== suggested?.id)) options.push({ kind: 'pickGroup', name: text })
  if (!visible.some((g) => same(g.name, text))) {
    options.push({ kind: 'newGroup', draft: { group: text, name: text, groupExists: false } })
  }
  return options
}
