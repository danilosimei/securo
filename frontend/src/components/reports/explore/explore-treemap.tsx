/** The current Explore level as a treemap: one tile per row, sized by what it
 *  cost, so the big ones are obvious before reading the table. Up to 12 tiles
 *  plus "Other (n)"; rows that take money off (refunds) stay out. A click
 *  goes down a level, exactly like the row below. */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { BreakdownRow } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatMoneyShort, formatPercent, useElementWidth, useIsNarrow } from '@/components/reports/common/chart-utils'
import { ChartTooltip, TooltipRow } from '@/components/reports/common/report-block'
import { NEUTRAL_COLOR, tint } from '@/components/reports/common/group-colors'
import { squarify } from './squarify'

const MAX_TILES = 12
const GAP = 2

interface Tile {
  key: string
  label: string
  value: number
  color: string
  row?: BreakdownRow
  rest?: BreakdownRow[]
}

interface TreemapProps {
  rows: BreakdownRow[]
  level: 'group' | 'category' | 'merchant'
  currency: string
  locale: string
  colorOf: (row: BreakdownRow) => string
  onOpen: (row: BreakdownRow) => void
  selected?: string | null
}

export function ExploreTreemap({ rows, level, currency, locale, colorOf, onOpen, selected }: TreemapProps) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const narrow = useIsNarrow()
  const [ref, width] = useElementWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const height = narrow ? 180 : 220

  const positive = rows.filter((r) => Number(r.total) > 0).sort((a, b) => Number(b.total) - Number(a.total))
  if (positive.length === 0) return null
  const total = positive.reduce((s, r) => s + Number(r.total), 0)
  const shown = positive.length > MAX_TILES + 1 ? positive.slice(0, MAX_TILES) : positive
  const rest = positive.slice(shown.length)
  const tiles: Tile[] = shown.map((row) => ({
    key: row.id ?? row.label,
    label: row.label,
    value: Number(row.total),
    color: colorOf(row),
    row,
  }))
  if (rest.length > 0) {
    tiles.push({
      key: '__rest',
      label: t('reports.explore.treemap.rest', { n: rest.length }),
      value: rest.reduce((s, r) => s + Number(r.total), 0),
      color: NEUTRAL_COLOR,
      rest,
    })
  }
  const rects = squarify(
    tiles.map((tile) => tile.value),
    { x: 0, y: 0, w: width, h: height },
  )
  const top = tiles[0]
  const answer = t('reports.explore.treemap.aria', {
    name: top.label,
    share: formatPercent(top.value / total, locale),
  })
  const hovered = hover !== null ? { tile: tiles[hover], rect: rects[hover] } : null

  return (
    <div ref={ref} className="relative w-full" style={{ height }} role="group" aria-label={answer} data-testid="explore-treemap" onMouseLeave={() => setHover(null)}>
      {tiles.map((tile, i) => {
        const r = rects[i]
        const w = Math.max(0, r.w - GAP)
        const h = Math.max(0, r.h - GAP)
        const roomy = w >= 84 && h >= 46
        const fits = w >= 46 && h >= 24
        const share = formatPercent(tile.value / total, locale)
        const label = `${tile.label}: ${mask(formatCurrency(tile.value, currency, locale))} (${share})`
        const style = {
          left: r.x,
          top: r.y,
          width: w,
          height: h,
          background: tint(tile.color, hover === i ? 34 : 22),
          borderTop: `3px solid ${tile.color}`,
        }
        // The visible text is drawn from data attributes: the tile already
        // carries its full aria-label, and the table below lists the same
        // names, so the page text keeps one copy of each.
        const content = fits && (
          <span aria-hidden="true" className="pointer-events-none block min-w-0 overflow-hidden px-2 pt-1 text-left leading-tight">
            <span data-text={tile.label} className="block truncate text-xs font-semibold text-foreground before:content-[attr(data-text)]" />
            {roomy && (
              <span
                data-text={`${mask(formatMoneyShort(tile.value, currency, locale))} · ${share}`}
                className="block truncate text-[11px] tabular-nums text-muted-foreground before:content-[attr(data-text)]"
              />
            )}
          </span>
        )
        const common = {
          style,
          'aria-label': label,
          'data-testid': tile.rest ? 'treemap-rest' : 'treemap-tile',
          onMouseEnter: () => setHover(i),
          onFocus: () => setHover(i),
          onBlur: () => setHover(null),
        }
        return tile.row ? (
          <button
            key={tile.key}
            {...common}
            type="button"
            onClick={() => onOpen(tile.row!)}
            data-selected={selected != null && tile.row.id === selected}
            className="absolute overflow-hidden rounded-[4px] transition-[background] duration-150 focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring data-[selected=true]:ring-2 data-[selected=true]:ring-primary motion-reduce:transition-none"
          >
            {content}
          </button>
        ) : (
          <div key={tile.key} {...common} role="img" tabIndex={0} className="absolute overflow-hidden rounded-[4px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            {content}
          </div>
        )
      })}
      {hovered && (
        <ChartTooltip x={hovered.rect.x + hovered.rect.w / 2} y={hovered.rect.y + 8} width={width}>
          <p className="mb-1 font-semibold text-foreground">{hovered.tile.label}</p>
          <TooltipRow
            color={hovered.tile.color}
            label={t(`reports.explore.treemap.of.${level}`)}
            value={`${mask(formatCurrency(hovered.tile.value, currency, locale))} · ${formatPercent(hovered.tile.value / total, locale)}`}
            strong
          />
          {hovered.tile.rest && (
            <p className="mt-1 text-muted-foreground">{hovered.tile.rest.slice(0, 6).map((r) => r.label).join(', ')}{hovered.tile.rest.length > 6 ? '…' : ''}</p>
          )}
          {hovered.tile.row && <p className="mt-1 text-[11px] font-medium text-primary">{t('reports.explore.treemap.openHint')}</p>}
        </ChartTooltip>
      )}
    </div>
  )
}
