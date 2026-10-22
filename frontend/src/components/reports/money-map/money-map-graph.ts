/** The nodes and links of the money map flow, built from its model. */
import {
  HUB_COLOR,
  LEFT_COLOR,
  NEUTRAL_COLOR,
  SHORT_COLOR,
  shade,
  type GroupPalette,
} from '@/components/reports/common/group-colors'
import type { MapGroup, MapItem, MapModel } from './money-map-model'

export type MapNodeKind = 'income' | 'short' | 'hub' | 'group' | 'folded' | 'left' | 'category'

export interface MapNode {
  id: string
  kind: MapNodeKind
  label: string
  amount: number
  color: string
  group?: MapGroup
  item?: MapItem
}

export interface MapLink {
  source: string
  target: string
  value: number
  color: string
}


const groupKey = (g: MapGroup) => g.id ?? `label:${g.label}`

export function buildGraph(
  model: MapModel,
  palette: GroupPalette,
  labels: { hubIncome: string; hubSpending: string; left: string; short: string; folded: string },
) {
  const nodes: MapNode[] = []
  const links: MapLink[] = []
  const short = model.left < 0
  const hub: MapNode = {
    id: 'hub',
    kind: 'hub',
    label: short ? labels.hubSpending : labels.hubIncome,
    amount: short ? model.totalSpending : model.totalIncome,
    color: HUB_COLOR,
  }
  for (const item of model.income) {
    const color = palette.group(item.id, item.label) === NEUTRAL_COLOR ? LEFT_COLOR : palette.group(item.id, item.label)
    nodes.push({ id: `in:${item.id ?? item.label}`, kind: 'income', label: item.label, amount: item.value, color, item })
    links.push({ source: `in:${item.id ?? item.label}`, target: 'hub', value: item.value, color })
  }
  if (short) {
    nodes.push({ id: 'short', kind: 'short', label: labels.short, amount: -model.left, color: SHORT_COLOR })
    links.push({ source: 'short', target: 'hub', value: -model.left, color: SHORT_COLOR })
  }
  nodes.push(hub)
  const categoryNodes: MapNode[] = []
  for (const group of model.groups) {
    const color = palette.group(group.id, group.label)
    const id = `g:${groupKey(group)}`
    nodes.push({ id, kind: 'group', label: group.label, amount: group.value, color, group })
    links.push({ source: 'hub', target: id, value: group.value, color })
    // A group whose only category carries its own name adds nothing.
    const cats = group.categories.length === 1 && group.categories[0].label === group.label ? [] : group.categories
    cats.forEach((cat, i) => {
      const own = palette.category(cat.id, cat.label)
      const catColor = own === NEUTRAL_COLOR ? shade(color, i) : own
      const cid = `c:${groupKey(group)}:${cat.id ?? cat.label}`
      categoryNodes.push({ id: cid, kind: 'category', label: cat.label, amount: cat.value, color: catColor, group, item: cat })
      links.push({ source: id, target: cid, value: cat.value, color: catColor })
    })
  }
  if (model.folded) {
    nodes.push({ id: 'folded', kind: 'folded', label: labels.folded, amount: model.folded.value, color: NEUTRAL_COLOR })
    links.push({ source: 'hub', target: 'folded', value: model.folded.value, color: NEUTRAL_COLOR })
  }
  if (model.left > 0) {
    nodes.push({ id: 'left', kind: 'left', label: labels.left, amount: model.left, color: LEFT_COLOR })
    links.push({ source: 'hub', target: 'left', value: model.left, color: LEFT_COLOR })
  }
  nodes.push(...categoryNodes)
  return { nodes, links }
}
