/** Spending composition month by month: one layer per group, stacked, so the
 *  height is the month's spending and each band is a group's part of it. The
 *  legend turns groups on and off; the crosshair reads every group of the
 *  month at once. */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatMoneyShort, niceTicks, useElementWidth } from '@/components/reports/common/chart-utils'
import { ChartTooltip, TooltipRow } from '@/components/reports/common/report-block'
import { CategoryGlyph } from '@/components/reports/common/category-glyph'
import type { GlyphSpec } from '@/components/reports/common/glyph-of'
import type { Layer } from './trends-model'

const HEIGHT = 260
const PAD = { top: 10, right: 10, bottom: 24, left: 52 }

interface StackedAreaProps {
  layers: Layer[]
  months: string[]
  colorOf: (layer: Layer) => string
  glyphOf: (layer: Layer) => GlyphSpec | null
  currency: string
  locale: string
  ariaLabel: string
}

export function StackedArea({ layers, months, colorOf, glyphOf, currency, locale, ariaLabel }: StackedAreaProps) {
  const { t } = useTranslation()
  const { mask, privacyMode } = usePrivacyMode()
  const [ref, width] = useElementWidth<HTMLDivElement>()
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [active, setActive] = useState<number | null>(null)

  const visible = layers.filter((l) => !hidden.has(l.key))
  const n = months.length
  const totals = months.map((_, i) => visible.reduce((s, l) => s + (l.values[i] ?? 0), 0))
  const ticks = niceTicks(Math.max(...totals, 1), 4)
  const top = ticks[ticks.length - 1]
  const plotW = Math.max(10, width - PAD.left - PAD.right)
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const x = (i: number) => PAD.left + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW)
  const y = (v: number) => PAD.top + plotH - (v / top) * plotH

  // Stack bottom-up in layer order (biggest group at the base).
  const base = months.map(() => 0)
  const bands = visible.map((layer) => {
    const lower = [...base]
    const upper = months.map((_, i) => (base[i] += layer.values[i] ?? 0))
    const d = [
      ...upper.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`),
      ...lower.map((_, j) => {
        const i = n - 1 - j
        return `L${x(i).toFixed(1)},${y(lower[i]).toFixed(1)}`
      }),
      'Z',
    ].join(' ')
    const edge = upper.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
    return { layer, d, edge }
  })

  const toggle = (key: string) =>
    setHidden((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else if (next.size < layers.length - 1) next.add(key)
      return next
    })

  const onMove = (e: React.MouseEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    const rel = e.clientX - box.left
    const i = n <= 1 ? 0 : Math.round((rel / Math.max(1, box.width)) * (n - 1))
    setActive(Math.max(0, Math.min(n - 1, i)))
  }
  const labelEvery = plotW / n < 34 ? 2 : 1
  const monthShort = (m: string) =>
    new Date(`${m}-01T00:00:00`).toLocaleDateString(locale, { month: 'short' }).replace('.', '')

  return (
    <div>
      <ul className="mb-3 flex flex-wrap gap-1.5" aria-label={t('reports.trends.legend')}>
        {layers.map((layer) => {
          const on = !hidden.has(layer.key)
          const glyph = glyphOf(layer)
          return (
            <li key={layer.key}>
              <button
                type="button"
                aria-pressed={on}
                onClick={() => toggle(layer.key)}
                data-testid="trends-legend-item"
                className={cn(
                  'flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  on ? 'border-border bg-card text-foreground' : 'border-dashed border-border bg-transparent text-muted-foreground line-through',
                )}
              >
                {glyph ? (
                  <span className={cn('flex', !on && 'opacity-50')}>
                    <CategoryGlyph icon={glyph.icon} color={glyph.color} size={14} />
                  </span>
                ) : (
                  <span className="h-2.5 w-2.5 rounded-sm" style={{ background: on ? colorOf(layer) : 'transparent', border: `1.5px solid ${colorOf(layer)}` }} />
                )}
                {layer.label}
              </button>
            </li>
          )
        })}
      </ul>
      <div ref={ref} className="relative w-full" onMouseLeave={() => setActive(null)}>
        <svg width={width} height={HEIGHT} role="img" aria-label={ariaLabel} data-testid="trends-area" className="block">
          {ticks.map((tick) => (
            <g key={tick}>
              <line x1={PAD.left} x2={width - PAD.right} y1={y(tick)} y2={y(tick)} stroke="var(--border)" />
              <text x={PAD.left - 8} y={y(tick)} dy="0.32em" textAnchor="end" fontSize={10} fill="var(--muted-foreground)" className="tabular-nums">
                {privacyMode ? '' : tick === 0 ? '0' : formatMoneyShort(tick, currency, locale)}
              </text>
            </g>
          ))}
          {bands.map(({ layer, d, edge }) => (
            <g key={layer.key}>
              <path d={d} style={{ fill: colorOf(layer) }} fillOpacity={layer.rest ? 0.35 : 0.72} />
              {/* The surface gap between layers. */}
              <path d={edge} fill="none" stroke="var(--card)" strokeWidth={1.5} />
            </g>
          ))}
          {months.map((m, i) =>
            i % labelEvery === 0 ? (
              <text key={m} x={x(i)} y={HEIGHT - 6} textAnchor="middle" fontSize={10} fill="var(--muted-foreground)">
                {monthShort(m)}
              </text>
            ) : null,
          )}
          {active !== null && (
            <line x1={x(active)} x2={x(active)} y1={PAD.top} y2={PAD.top + plotH} stroke="var(--foreground)" strokeOpacity={0.4} />
          )}
          <rect x={PAD.left} y={PAD.top} width={plotW} height={plotH} fill="transparent" onMouseMove={onMove} />
        </svg>
        {active !== null && (
          <ChartTooltip x={x(active)} y={PAD.top} width={width}>
            <p className="mb-1 font-semibold capitalize text-foreground">
              {new Date(`${months[active]}-01T00:00:00`).toLocaleDateString(locale, { month: 'long', year: 'numeric' })}
            </p>
            <TooltipRow label={t('reports.trends.monthTotal')} value={mask(formatCurrency(totals[active], currency, locale))} strong />
            {[...visible].reverse().map((layer) => (
              <TooltipRow key={layer.key} color={colorOf(layer)} label={layer.label} value={mask(formatCurrency(layer.values[active] ?? 0, currency, locale))} />
            ))}
          </ChartTooltip>
        )}
      </div>
    </div>
  )
}
