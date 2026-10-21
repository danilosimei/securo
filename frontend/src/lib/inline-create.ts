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

const same = (a: string, b: string) => normalizeText(a.trim()) === normalizeText(b.trim())

/**
 * What a picker offers to create from its search text, Notion style.
 *
 * `Group / Name` (cut at the first slash) creates straight in that group,
 * spelled as the existing group when one matches, or offers nothing when
 * the group already has the name. A plain name offers, in order: the
 * suggested group, picking another group, and a new group of the same name
 * (it is born with that one category). Nothing is offered for a name some
 * category already carries: picking it is the answer. Names compare without
 * case or accents, as the server does.
 */
export function groupedCreateOptions(
  search: string,
  categories: Category[],
  groups: CategoryGroup[],
  suggestedGroupId: string | null,
): GroupedCreateOption[] {
  const text = search.trim()
  if (!text) return []

  const slash = text.indexOf('/')
  if (slash >= 0) {
    const groupName = text.slice(0, slash).trim()
    const name = text.slice(slash + 1).trim()
    if (!groupName || !name) return []
    const existing = groups.find((g) => same(g.name, groupName))
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
  const suggested = suggestedGroupId ? groups.find((g) => g.id === suggestedGroupId) : undefined
  if (suggested) {
    options.push({ kind: 'inGroup', draft: { group: suggested.name, name: text, groupExists: true } })
  }
  if (groups.some((g) => g.id !== suggested?.id)) options.push({ kind: 'pickGroup', name: text })
  if (!groups.some((g) => same(g.name, text))) {
    options.push({ kind: 'newGroup', draft: { group: text, name: text, groupExists: false } })
  }
  return options
}
