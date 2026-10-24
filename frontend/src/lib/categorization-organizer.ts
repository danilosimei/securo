/** The category tree an organizer proposal leaves behind, predicted in the
 *  browser so the person sees the result before approving anything.
 *
 *  The ops name categories and groups by the proposal's short ids (`c1`,
 *  `g2`), whose mapping stays on the server; `labels` carries the names they
 *  had when the proposal was made. So nodes are found by name: applied ops
 *  first teach what each short id is called now (a rename or a split already
 *  changed it), then the pending ops are played on the current tree in order. */
import type { CategorizationRun, Category, CategoryGroup, OrganizerOp, OrganizerView } from '@/types'

export interface PredictedCategory {
  key: string
  name: string
  icon: string | null
  color: string | null
  /** Created by the proposal (a new category or a split child). */
  isNew: boolean
  /** The current name, when the proposal renames it. */
  renamedFrom?: string
  /** The current group's name (null = no group), when the proposal moves it. */
  movedFrom?: string | null
  /** Where everything goes, when the proposal merges it away. */
  mergedInto?: string
  /** Pending ops that touch this category. */
  opIds: string[]
}

export interface PredictedGroup {
  /** Group id, `new:<name>` for a proposed group, '' for "no group". */
  key: string
  /** Null for the categories without a group. */
  name: string | null
  icon: string | null
  color: string | null
  isNew: boolean
  renamedFrom?: string
  categories: PredictedCategory[]
  /** Pending ops that touch the group itself. */
  opIds: string[]
  /** Something in the group changes. */
  touched: boolean
}

const SHORT_GROUP = /^g\d+$/
const SHORT_CATEGORY = /^c\d+$/
const UNGROUPED = ''

const norm = (value: string) => value.normalize('NFC').trim().toLocaleLowerCase()

interface CatNode extends PredictedCategory {
  groupKey: string
  /** Name in the current tree, which short ids resolve against. */
  original: string
}

interface GroupNode extends Omit<PredictedGroup, 'categories' | 'touched'> {
  original: string | null
}

function addOp(node: { opIds: string[] }, opId: string) {
  if (!node.opIds.includes(opId)) node.opIds.push(opId)
}

export function predictTree(
  groups: CategoryGroup[],
  categories: Category[],
  ops: OrganizerOp[],
): PredictedGroup[] {
  const groupNodes: GroupNode[] = groups.map((g) => ({
    key: g.id,
    name: g.name,
    icon: g.icon,
    color: g.color,
    isNew: false,
    opIds: [],
    original: g.name,
  }))
  const known = new Set(groupNodes.map((g) => g.key))
  const ungrouped: GroupNode = {
    key: UNGROUPED,
    name: null,
    icon: null,
    color: null,
    isNew: false,
    opIds: [],
    original: null,
  }
  const cats: CatNode[] = categories.map((c) => ({
    key: c.id,
    name: c.name,
    icon: c.icon,
    color: c.color,
    isNew: false,
    opIds: [],
    groupKey: c.group_id && known.has(c.group_id) ? c.group_id : UNGROUPED,
    original: c.name,
  }))

  // What each short id is called now, after the ops already applied.
  const alias = new Map<string, string | null>()
  for (const op of ops) {
    if (op.status !== 'applied' || !op.target) continue
    if (op.op === 'rename' && op.name) alias.set(op.target, op.name)
    else if (op.op === 'split_category' && op.children[0]) alias.set(op.target, op.children[0])
    else if (op.op === 'merge') alias.set(op.target, null)
  }

  const shortCats = new Map<string, CatNode | null>()
  const shortGroups = new Map<string, GroupNode | null>()

  const findCategory = (short: string | null, label: string | null | undefined): CatNode | null => {
    if (!short) return null
    const cached = shortCats.get(short)
    if (cached !== undefined) return cached
    const name = alias.has(short) ? alias.get(short) : label ?? (SHORT_CATEGORY.test(short) ? null : short)
    const node = name == null ? null : cats.find((c) => !c.isNew && norm(c.original) === norm(name)) ?? null
    shortCats.set(short, node)
    return node
  }

  const groupByName = (name: string) => groupNodes.find((g) => g.name !== null && norm(g.name) === norm(name))

  /** A group the op names by short id (existing) or by name (found or created). */
  const resolveGroup = (ref: string | null, label: string | null | undefined, opId: string): GroupNode | null => {
    if (!ref) return null
    if (SHORT_GROUP.test(ref)) {
      const cached = shortGroups.get(ref)
      if (cached !== undefined) return cached
      const name = alias.has(ref) ? alias.get(ref) : label
      const node = name == null ? null : groupNodes.find((g) => !g.isNew && g.original !== null && norm(g.original) === norm(name)) ?? null
      shortGroups.set(ref, node)
      if (node || !label || label === ref) return node
      // The id is unknown here: a group created under that literal name.
      return resolveGroup(label, label, opId)
    }
    const found = groupByName(ref)
    if (found) return found
    const created: GroupNode = {
      key: `new:${norm(ref)}`,
      name: ref,
      icon: null,
      color: null,
      isNew: true,
      opIds: [opId],
      original: null,
    }
    groupNodes.push(created)
    return created
  }

  const groupLabel = (key: string) => groupNodes.find((g) => g.key === key)?.name ?? null

  const moveTo = (cat: CatNode, group: GroupNode) => {
    if (cat.groupKey === group.key) return
    if (!cat.isNew && cat.movedFrom === undefined) cat.movedFrom = groupLabel(cat.groupKey)
    cat.groupKey = group.key
    // Arrives at the end of its new group, like a new category.
    cats.splice(cats.indexOf(cat), 1)
    cats.push(cat)
    // Moved back where it started: not a move after all.
    if (!cat.isNew && cat.movedFrom === groupLabel(group.key)) delete cat.movedFrom
  }

  const rename = (cat: CatNode, name: string) => {
    if (norm(cat.name) === norm(name)) return
    if (!cat.isNew && cat.renamedFrom === undefined) cat.renamedFrom = cat.name
    cat.name = name
    if (cat.renamedFrom !== undefined && norm(cat.renamedFrom) === norm(name)) delete cat.renamedFrom
  }

  const addCategory = (group: GroupNode, name: string, opId: string, like?: CatNode | null, besides?: CatNode) => {
    const existing = cats.find(
      (c) => c !== besides && c.groupKey === group.key && c.mergedInto === undefined && norm(c.name) === norm(name),
    )
    if (existing) {
      addOp(existing, opId)
      return
    }
    cats.push({
      key: `new:${group.key}:${norm(name)}`,
      name,
      icon: like?.icon ?? group.icon,
      color: like?.color ?? group.color,
      isNew: true,
      opIds: [opId],
      groupKey: group.key,
      original: name,
    })
  }

  for (const op of ops) {
    if (op.status !== 'pending') continue
    const labels = op.labels ?? {}
    switch (op.op) {
      case 'create_group': {
        if (!op.name) break
        const group = resolveGroup(op.name, op.name, op.id)
        if (group) addOp(group, op.id)
        break
      }
      case 'create_category': {
        const group = resolveGroup(op.group, labels.group, op.id)
        if (group && op.name) addCategory(group, op.name, op.id)
        break
      }
      case 'rename': {
        if (!op.name) break
        if (op.target && SHORT_GROUP.test(op.target)) {
          const group = resolveGroup(op.target, labels.target, op.id)
          if (!group || group.name === null) break
          if (!group.isNew && group.renamedFrom === undefined && norm(group.name) !== norm(op.name)) {
            group.renamedFrom = group.name
          }
          group.name = op.name
          addOp(group, op.id)
        } else {
          const cat = findCategory(op.target, labels.target)
          if (!cat) break
          rename(cat, op.name)
          addOp(cat, op.id)
        }
        break
      }
      case 'move_category': {
        const cat = findCategory(op.target, labels.target)
        const group = resolveGroup(op.group, labels.group, op.id)
        if (!cat || !group) break
        moveTo(cat, group)
        addOp(cat, op.id)
        break
      }
      case 'merge': {
        const cat = findCategory(op.target, labels.target)
        const into = findCategory(op.into, labels.into)
        if (!cat) break
        cat.mergedInto = into?.name ?? labels.into ?? op.into ?? ''
        addOp(cat, op.id)
        if (into) addOp(into, op.id)
        break
      }
      case 'split_category': {
        const cat = findCategory(op.target, labels.target)
        const [first, ...rest] = op.children
        if (!cat || !first) break
        const group = resolveGroup(op.name ?? cat.name, op.name ?? cat.name, op.id)
        if (!group) break
        rename(cat, first)
        moveTo(cat, group)
        addOp(cat, op.id)
        for (const child of rest) addCategory(group, child, op.id, cat, cat)
        break
      }
      default:
        break
    }
  }

  const result: PredictedGroup[] = []
  for (const group of [...groupNodes, ungrouped]) {
    const members = cats.filter((c) => c.groupKey === group.key)
    if (group.key === UNGROUPED && members.length === 0) continue
    result.push({
      key: group.key,
      name: group.name,
      icon: group.icon,
      color: group.color,
      isNew: group.isNew,
      renamedFrom: group.renamedFrom,
      opIds: group.opIds,
      categories: members.map((c) => ({
        key: c.key,
        name: c.name,
        icon: c.icon,
        color: c.color,
        isNew: c.isNew,
        renamedFrom: c.renamedFrom,
        movedFrom: c.movedFrom,
        mergedInto: c.mergedInto,
        opIds: c.opIds,
      })),
      touched: group.isNew || group.opIds.length > 0 || members.some((c) => c.opIds.length > 0),
    })
  }
  return result
}

/** The organize run is one synchronous Opus call (~1 min). */
const POLL_MS = 4000
const RUN_BUSY = new Set(['queued', 'running'])
const REDISTRIBUTION_BUSY = new Set(['queued', 'running', 'processing_batch'])

export const redistributionBusy = (run: CategorizationRun | null | undefined) =>
  !!run && REDISTRIBUTION_BUSY.has(run.status)

/** Poll while the proposal is being made or a split's rows are moving. */
export function organizerPollInterval(view: OrganizerView | undefined): number | false {
  if (!view) return false
  if (view.run && RUN_BUSY.has(view.run.status)) return POLL_MS
  return view.ops.some((op) => redistributionBusy(op.redistribution)) ? POLL_MS : false
}
