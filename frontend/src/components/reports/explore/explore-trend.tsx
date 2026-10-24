/** "Evolução": how the biggest rows of the list moved over the last 12
 *  months, one line each, and the rest summed in a gray line. The legend
 *  names every line (and opens it), the crosshair reads every line of the
 *  month at once, and the table below keeps every number. */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatMoneyShort, niceTicks, useElementWidth, useIsNarrow } from '@/components/reports/common/chart-utils'
import { ChartTooltip, TooltipRow } from '@/components/reports/common/report-block'
import type { BreakdownRow } from '@/lib/api'
import { CategoryGlyph } from '@/components/reports/common/category-glyph'
import type { Glyph, TrendSeries } from './explore-model'

const PAD = { top: 12, right: 12, bottom: 24, left: 52 }

interface ExploreTrendProps {
  series: TrendSeries[]
  months: string[]
  colorOf: (series: TrendSeries) => string
  onOpen: (row: BreakdownRow) => void
  currency: string
  locale: string
  ariaLabel: string
  /** The group's or category's icon before a line's name. */
  glyphOf?: (row: BreakdownRow) => Glyph
}

export function ExploreTrend({ series, months, colorOf, onOpen, currency, locale, ariaLabel, glyphOf }: ExploreTrendProps) {
  const { t } = useTranslation()
  const { mask, privacyMode } = usePrivacyMode()
  const narrow = useIsNarrow()
  const [ref, width] = useElementWidth<HTMLDivElement>()
  const [active, setActive] = useState<number | null>(null)
  const height = narrow ? 200 : 240
  const n = months.length
  if (series.length === 0 || n < 2) return null

  const max = Math.max(...series.flatMap((s) => s.values), 1)
  const ticks = niceTicks(max, 4)
  const top = ticks[ticks.length - 1]
  const plotW = Math.max(10, width - PAD.left - PAD.right)
  const plotH = height - PAD.top - PAD.bottom
  const x = (i: number) => PAD.left + (i / (n - 1)) * plotW
  const y = (v: number) => PAD.top + plotH - (Math.max(0, v) / top) * plotH
  const path = (values: number[]) =>
    values.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    const i = Math.round(((e.clientX - box.left) / Math.max(1, box.width)) * (n - 1))
    setActive(Math.max(0, Math.min(n - 1, i)))
  }
  const labelEvery = plotW / n < 34 ? 2 : 1
  const monthShort = (m: string) =>
    new Date(`${m}-01T00:00:00`).toLocaleDateString(locale, { month: 'short' }).replace('.', '')
  // The rest is drawn first, under the named lines.
  const drawOrder = [...series].sort((a, b) => Number(!a.rest) - Number(!b.rest))

  return (
    <div data-testid="explore-trend">
      <ul className="mb-3 flex flex-wrap gap-x-3 gap-y-1.5" aria-label={t('reports.explore.trend.legend')}>
        {series.map((s) => (
          <li key={s.key}>
            {s.row ? (
              <button
                type="button"
                onClick={() => onOpen(s.row!)}
                className="flex items-center gap-1.5 rounded text-xs font-medium text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span aria-hidden="true" className="h-0.5 w-3.5 rounded-full" style={{ background: colorOf(s) }} />
                {glyphOf && <CategoryGlyph icon={glyphOf(s.row).icon} color={glyphOf(s.row).color} size={12} />}
                {s.label}
              </button>
            ) : (
              <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <span aria-hidden="true" className="h-0.5 w-3.5 rounded-full" style={{ background: colorOf(s) }} />
                {s.label}
              </span>
            )}
          </li>
        ))}
      </ul>
      <div ref={ref} className="relative w-full" onPointerLeave={() => setActive(null)}>
        <svg width={width} height={height} role="img" aria-label={ariaLabel} className="block">
          {ticks.map((tick) => (
            <g key={tick}>
              <line x1={PAD.left} x2={width - PAD.right} y1={y(tick)} y2={y(tick)} stroke="var(--border)" />
              <text x={PAD.left - 8} y={y(tick)} dy="0.32em" textAnchor="end" fontSize={10} fill="var(--muted-foreground)" className="tabular-nums">
                {privacyMode ? '' : tick === 0 ? '0' : formatMoneyShort(tick, currency, locale)}
              </text>
            </g>
          ))}
          {months.map((m, i) =>
            i % labelEvery === 0 ? (
              <text key={m} x={x(i)} y={height - 6} textAnchor="middle" fontSize={10} fill="var(--muted-foreground)">
                {monthShort(m)}
              </text>
            ) : null,
          )}
          {active !== null && (
            <line x1={x(active)} x2={x(active)} y1={PAD.top} y2={PAD.top + plotH} stroke="var(--foreground)" strokeOpacity={0.35} />
          )}
          {drawOrder.map((s) => (
            <g key={s.key}>
              <path d={path(s.values)} fill="none" stroke={colorOf(s)} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
              <circle cx={x(n - 1)} cy={y(s.values[n - 1] ?? 0)} r={4} fill={colorOf(s)} stroke="var(--card)" strokeWidth={2} />
              {active !== null && (
                <circle cx={x(active)} cy={y(s.values[active] ?? 0)} r={4} fill={colorOf(s)} stroke="var(--card)" strokeWidth={2} />
              )}
            </g>
          ))}
          <rect x={PAD.left} y={PAD.top} width={plotW} height={plotH} fill="transparent" onPointerMove={onMove} onPointerDown={onMove} />
        </svg>
        {active !== null && (
          <ChartTooltip x={x(active)} y={PAD.top} width={width}>
            <p className="mb-1 font-semibold capitalize text-foreground">
              {new Date(`${months[active]}-01T00:00:00`).toLocaleDateString(locale, { month: 'long', year: 'numeric' })}
            </p>
            {[...series]
              .sort((a, b) => (b.values[active] ?? 0) - (a.values[active] ?? 0))
              .map((s) => (
                <TooltipRow key={s.key} color={colorOf(s)} label={s.label} value={mask(formatCurrency(s.values[active] ?? 0, currency, locale))} />
              ))}
          </ChartTooltip>
        )}
      </div>
    </div>
  )
}
