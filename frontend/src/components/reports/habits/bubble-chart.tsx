/** How often × how much: each store is a bubble, placed by purchases per month
 *  (across) and average ticket (up), sized by what it cost in total and
 *  colored by its group. Both axes are logarithmic so a R$ 8 coffee and a
 *  R$ 4,000 bill both fit. Thin lines at the medians split the plane, and
 *  three of its corners are named: costly habit, big and rare, small stuff. */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { HabitsMerchant } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatMoneyShort, useElementWidth } from '@/components/reports/common/chart-utils'
import { ChartTooltip, TooltipRow } from '@/components/reports/common/report-block'
import { quadrants } from './habits-model'

const HEIGHT = 320
const PAD = { top: 14, right: 16, bottom: 34, left: 56 }
const MAX_R = 26
const MIN_R = 4
const LABELS = 6

const TICKS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000]

interface BubbleChartProps {
  merchants: HabitsMerchant[]
  currency: string
  locale: string
  colorOf: (m: HabitsMerchant) => string
  onOpen: (m: HabitsMerchant) => void
  ariaLabel: string
}

export function BubbleChart({ merchants, currency, locale, colorOf, onOpen, ariaLabel }: BubbleChartProps) {
  const { t } = useTranslation()
  const { mask, privacyMode } = usePrivacyMode()
  const [ref, width] = useElementWidth<HTMLDivElement>()
  const [active, setActive] = useState<string | null>(null)

  const points = merchants.filter((m) => m.per_month > 0 && Number(m.avg_ticket) > 0)
  const xs = points.map((m) => m.per_month)
  const ys = points.map((m) => Number(m.avg_ticket))
  const span = (values: number[]) => {
    const lo = Math.min(...values)
    const hi = Math.max(...values)
    // Half a decade of air on each side keeps bubbles off the edges.
    return [Math.log10(lo) - 0.25, Math.log10(Math.max(hi, lo * 1.5)) + 0.25]
  }
  const [x0, x1] = span(xs)
  const [y0, y1] = span(ys)
  const plotW = Math.max(10, width - PAD.left - PAD.right)
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const x = (v: number) => PAD.left + ((Math.log10(v) - x0) / (x1 - x0)) * plotW
  const y = (v: number) => PAD.top + plotH - ((Math.log10(v) - y0) / (y1 - y0)) * plotH
  const maxTotal = Math.max(...points.map((m) => Number(m.total)), 1)
  const r = (m: HabitsMerchant) => Math.max(MIN_R, Math.sqrt(Number(m.total) / maxTotal) * MAX_R)
  const q = quadrants(points)
  const xTicks = TICKS.filter((v) => Math.log10(v) >= x0 && Math.log10(v) <= x1)
  const yTicks = TICKS.filter((v) => Math.log10(v) >= y0 && Math.log10(v) <= y1)
  const labelled = new Set(
    [...points].sort((a, b) => Number(b.total) - Number(a.total)).slice(0, LABELS).map((m) => m.id),
  )
  // Biggest first, so the small ones sit on top and stay reachable.
  const order = [...points].sort((a, b) => Number(b.total) - Number(a.total))
  const hovered = active ? points.find((m) => m.id === active) : undefined
  const corner = 'pointer-events-none fill-[var(--muted-foreground)] text-[10px] font-semibold uppercase tracking-wide opacity-60'

  return (
    <div ref={ref} className="relative w-full" onMouseLeave={() => setActive(null)}>
      <svg width={width} height={HEIGHT} role="group" aria-label={ariaLabel} data-testid="habits-bubbles" className="block">
        <g aria-hidden="true">
          {yTicks.map((v) => (
            <g key={`y${v}`}>
              <line x1={PAD.left} x2={width - PAD.right} y1={y(v)} y2={y(v)} stroke="var(--border)" />
              <text x={PAD.left - 8} y={y(v)} dy="0.32em" textAnchor="end" fontSize={10} fill="var(--muted-foreground)" className="tabular-nums">
                {privacyMode ? '' : formatMoneyShort(v, currency, locale)}
              </text>
            </g>
          ))}
          {xTicks.map((v) => (
            <text key={`x${v}`} x={x(v)} y={HEIGHT - 18} textAnchor="middle" fontSize={10} fill="var(--muted-foreground)" className="tabular-nums">
              {v.toLocaleString(locale)}
            </text>
          ))}
          <text x={PAD.left + plotW / 2} y={HEIGHT - 3} textAnchor="middle" fontSize={10} fill="var(--muted-foreground)">
            {t('reports.habits.bubbles.xAxis')}
          </text>
          <text x={12} y={PAD.top + plotH / 2} textAnchor="middle" fontSize={10} fill="var(--muted-foreground)" transform={`rotate(-90 12 ${PAD.top + plotH / 2})`}>
            {t('reports.habits.bubbles.yAxis')}
          </text>
          {q.freq > 0 && <line x1={x(q.freq)} x2={x(q.freq)} y1={PAD.top} y2={PAD.top + plotH} stroke="var(--muted-foreground)" strokeOpacity={0.35} />}
          {q.ticket > 0 && <line x1={PAD.left} x2={width - PAD.right} y1={y(q.ticket)} y2={y(q.ticket)} stroke="var(--muted-foreground)" strokeOpacity={0.35} />}
          <text x={width - PAD.right - 4} y={PAD.top + 12} textAnchor="end" className={corner}>{t('reports.habits.bubbles.costlyHabit')}</text>
          <text x={PAD.left + 6} y={PAD.top + 12} className={corner}>{t('reports.habits.bubbles.bigRare')}</text>
          <text x={width - PAD.right - 4} y={PAD.top + plotH - 6} textAnchor="end" className={corner}>{t('reports.habits.bubbles.small')}</text>
        </g>
        {order.map((m) => {
          const cx = x(m.per_month)
          const cy = y(Number(m.avg_ticket))
          const radius = r(m)
          const on = !active || active === m.id
          return (
            <g
              key={m.id}
              role="button"
              tabIndex={0}
              data-testid="habit-bubble"
              aria-label={t('reports.habits.bubbles.aria', {
                name: m.label,
                perMonth: m.per_month.toLocaleString(locale, { maximumFractionDigits: 1 }),
                ticket: mask(formatCurrency(Number(m.avg_ticket), currency, locale)),
                total: mask(formatCurrency(Number(m.total), currency, locale)),
              })}
              className="cursor-pointer outline-none [&:focus-visible>circle:nth-child(2)]:stroke-[var(--ring)]"
              onMouseEnter={() => setActive(m.id)}
              onFocus={() => setActive(m.id)}
              onBlur={() => setActive(null)}
              onClick={() => onOpen(m)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onOpen(m)
                }
              }}
            >
              {/* A target at least 24px across, bigger than small bubbles. */}
              <circle cx={cx} cy={cy} r={Math.max(12, radius + 3)} fill="transparent" />
              <circle
                cx={cx}
                cy={cy}
                r={radius}
                style={{ fill: colorOf(m) }}
                fillOpacity={on ? 0.62 : 0.18}
                stroke="var(--card)"
                strokeWidth={2}
              />
            </g>
          )
        })}
        <g aria-hidden="true" pointerEvents="none">
          {order
            .filter((m) => labelled.has(m.id))
            .map((m) => (
              <text
                key={m.id}
                x={x(m.per_month)}
                y={y(Number(m.avg_ticket)) - r(m) - 4}
                textAnchor="middle"
                fontSize={11}
                fontWeight={600}
                fill="var(--foreground)"
                stroke="var(--card)"
                strokeWidth={3}
                paintOrder="stroke"
              >
                {m.label.length > 18 ? `${m.label.slice(0, 17)}…` : m.label}
              </text>
            ))}
        </g>
      </svg>
      {hovered && (
        <ChartTooltip x={x(hovered.per_month)} y={y(Number(hovered.avg_ticket))} width={width}>
          <p className="font-semibold text-foreground">{hovered.label}</p>
          <p className="mb-1 text-muted-foreground">{hovered.category}</p>
          <TooltipRow color={colorOf(hovered)} label={t('reports.habits.bubbles.total')} value={mask(formatCurrency(Number(hovered.total), currency, locale))} strong />
          <TooltipRow label={t('reports.habits.bubbles.ticket')} value={mask(formatCurrency(Number(hovered.avg_ticket), currency, locale))} />
          <TooltipRow
            label={t('reports.habits.bubbles.perMonthLabel')}
            value={hovered.per_month.toLocaleString(locale, { maximumFractionDigits: 1 })}
          />
          <p className="mt-1 text-[11px] font-medium text-primary">{t('reports.habits.openHint')}</p>
        </ChartTooltip>
      )}
    </div>
  )
}
