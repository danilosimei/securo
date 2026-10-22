/** The money map as a flow: income sources → all the money → spending groups
 *  (and what was left) → the categories inside each group. Every group and
 *  category keeps its own color; nodes carry their value and share, the
 *  tooltip adds the share of income and of the group, and a click on a group
 *  or category opens it in Explore. */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { sankey, sankeyLeft, sankeyLinkHorizontal, type SankeyGraph } from 'd3-sankey'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatMoneyShort, formatPercent, useElementWidth } from '@/components/reports/common/chart-utils'
import { ChartTooltip, TooltipRow } from '@/components/reports/common/report-block'
import type { GroupPalette } from '@/components/reports/common/group-colors'
import type { MapModel } from './money-map-model'
import { buildGraph, type MapLink, type MapNode } from './money-map-graph'

type LaidNode = MapNode & { x0: number; x1: number; y0: number; y1: number; index: number }

const NODE_W = 10
const LABEL_FONT = 11
const CHAR_W = 6.1

interface SankeyProps {
  model: MapModel
  palette: GroupPalette
  currency: string
  locale: string
  onOpen: (node: MapNode) => void
  onExpand: () => void
  ariaLabel: string
}

export function MoneyMapSankey({ model, palette, currency, locale, onOpen, onExpand, ariaLabel }: SankeyProps) {
  const { t } = useTranslation()
  const { mask, privacyMode, MASK } = usePrivacyMode()
  const [ref, width] = useElementWidth<HTMLDivElement>(900)
  const [hover, setHover] = useState<string | null>(null)

  const graph = useMemo(
    () =>
      buildGraph(model, palette, {
        hubIncome: t('reports.moneyMap.hubIncome'),
        hubSpending: t('reports.moneyMap.hubSpending'),
        left: t('reports.moneyMap.left'),
        short: t('reports.moneyMap.short'),
        folded: t('reports.moneyMap.folded', { n: model.folded?.groups.length ?? 0 }),
      }),
    [model, palette, t],
  )
  const categoryCount = graph.nodes.filter((n) => n.kind === 'category').length
  const middleCount = graph.nodes.filter((n) => n.kind === 'group' || n.kind === 'folded' || n.kind === 'left').length
  const height = Math.round(Math.min(1100, Math.max(380, Math.max(categoryCount * 20, middleCount * 34) + 24)))
  const right = width < 760 ? 132 : 172

  const layout = useMemo(() => {
    const generator = sankey<MapNode, MapLink>()
      .nodeId((n) => n.id)
      .nodeWidth(NODE_W)
      .nodePadding(categoryCount > 30 ? 4 : 8)
      .nodeAlign(sankeyLeft)
      .nodeSort(null as never)
      .linkSort(null as never)
      .extent([
        [2, 8],
        [width - right, height - 8],
      ])
    // d3-sankey mutates its input: hand it copies.
    return generator({
      nodes: graph.nodes.map((n) => ({ ...n })),
      links: graph.links.map((l) => ({ ...l })),
    }) as unknown as SankeyGraph<MapNode, MapLink> & { nodes: LaidNode[] }
  }, [graph, width, height, right, categoryCount])

  const nodes = layout.nodes as LaidNode[]
  const hoverNode = hover ? nodes.find((n) => n.id === hover) : undefined
  const lit = useMemo(() => {
    const set = new Set<string>()
    if (!hover) return set
    set.add(hover)
    for (const link of layout.links) {
      const s = (link.source as LaidNode).id
      const tg = (link.target as LaidNode).id
      if (s === hover || tg === hover) {
        set.add(s)
        set.add(tg)
      }
    }
    return set
  }, [hover, layout])

  // Labels: biggest nodes first; a label that would collide with one already
  // placed in its column drops to one line, then to the tooltip.
  const labels = useMemo(() => {
    const placed = new Map<number, { y: number; lines: 1 | 2 }>()
    const columns = new Map<number, { top: number; bottom: number }[]>()
    const order = [...nodes].sort((a, b) => b.amount - a.amount)
    for (const n of order) {
      const col = Math.round(n.x0)
      const taken = columns.get(col) ?? []
      const center = (n.y0 + n.y1) / 2
      const fits = (half: number) =>
        center - half >= 0 && center + half <= height && taken.every((s) => center + half <= s.top || center - half >= s.bottom)
      const lines: 1 | 2 | 0 = fits(13) ? 2 : fits(7) ? 1 : 0
      if (!lines) continue
      const half = lines === 2 ? 13 : 7
      taken.push({ top: center - half, bottom: center + half })
      columns.set(col, taken)
      placed.set(n.index, { y: center, lines })
    }
    return placed
  }, [nodes, height])

  const money = (v: number) => (privacyMode ? MASK : formatMoneyShort(v, currency, locale))
  // Each node's share is of what it splits: a category of its group, a group
  // of the spending, income and what is left or missing of the income. The
  // hub is the whole, so it has none.
  const share = (n: MapNode): number | null => {
    if (n.kind === 'hub') return null
    if (n.kind === 'category' && n.group) return n.amount / n.group.value
    const base = n.kind === 'group' || n.kind === 'folded' || model.totalIncome <= 0 ? model.totalSpending : model.totalIncome
    return base > 0 ? n.amount / base : null
  }
  const shareText = (n: MapNode) => {
    const value = share(n)
    return value === null ? '' : formatPercent(value, locale)
  }
  const clickable = (n: MapNode) => n.kind === 'group' || n.kind === 'category' || n.kind === 'folded'
  const activate = (n: MapNode) => (n.kind === 'folded' ? onExpand() : onOpen(n))
  const path = sankeyLinkHorizontal<MapNode, MapLink>()
  const fit = (text: string, room: number) => {
    const max = Math.max(4, Math.floor(room / CHAR_W))
    return text.length > max ? `${text.slice(0, max - 1)}…` : text
  }
  const nodeAria = (n: MapNode) =>
    t('reports.moneyMap.nodeAria', {
      name: n.label,
      value: mask(formatCurrency(n.amount, currency, locale)),
      share: shareText(n) || '100%',
    }) + (clickable(n) ? `. ${t(n.kind === 'folded' ? 'reports.moneyMap.expandHint' : 'reports.moneyMap.openHint')}` : '')

  return (
    <div ref={ref} className="relative w-full" onMouseLeave={() => setHover(null)}>
      <svg width={width} height={height} role="group" aria-label={ariaLabel} data-testid="money-map-sankey" className="block">
        <g aria-hidden="true">
          {layout.links.map((link, i) => {
            const s = link.source as LaidNode
            const tg = link.target as LaidNode
            const on = !hover || (lit.has(s.id) && lit.has(tg.id))
            return (
              <path
                key={i}
                d={path(link) ?? undefined}
                fill="none"
                style={{ stroke: link.color }}
                strokeOpacity={on ? (hover ? 0.5 : 0.28) : 0.07}
                strokeWidth={Math.max(1, link.width ?? 1)}
                className="transition-[stroke-opacity] duration-150 motion-reduce:transition-none"
              />
            )
          })}
        </g>
        {nodes.map((n) => {
          const h = Math.max(1, n.y1 - n.y0)
          const on = !hover || lit.has(n.id)
          const label = labels.get(n.index)
          const last = n.kind === 'category' || ((n.kind === 'left' || n.kind === 'folded' || n.kind === 'group') && categoryCount === 0)
          const room = last ? right - 12 : (width - right) / 3 - 24
          const interactive = clickable(n)
          return (
            <g
              key={n.id}
              data-testid={`map-node-${n.kind}`}
              data-label={n.label}
              role={interactive ? 'button' : 'img'}
              tabIndex={interactive ? 0 : -1}
              aria-label={nodeAria(n)}
              className={interactive ? 'cursor-pointer outline-none [&:focus-visible>rect]:stroke-[var(--ring)]' : undefined}
              opacity={on ? 1 : 0.35}
              onMouseEnter={() => setHover(n.id)}
              onFocus={() => setHover(n.id)}
              onBlur={() => setHover(null)}
              onClick={interactive ? () => activate(n) : undefined}
              onKeyDown={
                interactive
                  ? (e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        activate(n)
                      }
                    }
                  : undefined
              }
            >
              <rect
                x={n.x0}
                y={n.y0}
                width={n.x1 - n.x0}
                height={h}
                rx={2}
                style={{ fill: n.color }}
                stroke="transparent"
                strokeWidth={2}
              />
              {/* A wider invisible target, so thin nodes are easy to hit. */}
              <rect x={n.x0 - 6} y={n.y0 - 3} width={n.x1 - n.x0 + 12 + (label ? Math.min(room, 120) : 0)} height={h + 6} fill="transparent" />
              {label && (
                <text
                  x={n.x1 + 6}
                  y={label.y}
                  fontSize={LABEL_FONT}
                  fill="var(--foreground)"
                  stroke="var(--card)"
                  strokeWidth={3}
                  strokeLinejoin="round"
                  paintOrder="stroke"
                  pointerEvents="none"
                >
                  {label.lines === 2 ? (
                    <>
                      <tspan x={n.x1 + 6} dy="-0.25em" fontWeight={600}>
                        {fit(n.label, room)}
                      </tspan>
                      <tspan x={n.x1 + 6} dy="1.25em" fill="var(--muted-foreground)" className="tabular-nums">
                        {shareText(n) ? `${money(n.amount)} · ${shareText(n)}` : money(n.amount)}
                      </tspan>
                    </>
                  ) : (
                    <tspan dy="0.35em">
                      <tspan fontWeight={600}>{fit(n.label, room - 60)}</tspan>
                      <tspan fill="var(--muted-foreground)" className="tabular-nums">{` ${money(n.amount)}`}</tspan>
                    </tspan>
                  )}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      {hoverNode && (
        <ChartTooltip x={hoverNode.x1} y={hoverNode.y0} width={width}>
          <p className="mb-1 font-semibold text-foreground">{hoverNode.label}</p>
          <TooltipRow color={hoverNode.color} label={t('reports.moneyMap.value')} value={mask(formatCurrency(hoverNode.amount, currency, locale))} strong />
          {model.totalIncome > 0 && hoverNode.kind !== 'hub' && (
            <TooltipRow label={t('reports.moneyMap.ofIncome')} value={formatPercent(hoverNode.amount / model.totalIncome, locale)} />
          )}
          {hoverNode.kind === 'group' && model.totalSpending > 0 && (
            <TooltipRow label={t('reports.moneyMap.ofSpending')} value={formatPercent(hoverNode.amount / model.totalSpending, locale)} />
          )}
          {hoverNode.kind === 'category' && hoverNode.group && (
            <TooltipRow
              label={t('reports.moneyMap.ofGroup', { group: hoverNode.group.label })}
              value={formatPercent(hoverNode.amount / hoverNode.group.value, locale)}
            />
          )}
          {clickable(hoverNode) && (
            <p className="mt-1 text-[11px] font-medium text-primary">
              {t(hoverNode.kind === 'folded' ? 'reports.moneyMap.expandHint' : 'reports.moneyMap.openHint')}
            </p>
          )}
        </ChartTooltip>
      )}
    </div>
  )
}
