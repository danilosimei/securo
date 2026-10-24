/** Month by month: spending as bars, the median month as a reference line and
 *  income as a line, all on one money axis. A month at 1.5× the median or
 *  more is the one that stands out, so it alone takes the strong color and a
 *  label; the rest stay quiet. Clicking a month opens Explore on it. */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import type { OverviewMonth } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import {
  formatMoneyShort,
  monthName,
  niceTicks,
  useElementWidth,
} from '@/components/reports/common/chart-utils'
import { ChartTooltip, TooltipRow } from '@/components/reports/common/report-block'
import { LEFT_COLOR } from '@/components/reports/common/group-colors'
import { monthExploreLink } from '@/components/reports/common/report-url'
import { SPIKE_MULTIPLE, peakMonth } from './overview-model'

const HEIGHT = 240
const PAD = { top: 22, right: 8, bottom: 26, left: 52 }
const BAR_COLOR = 'color-mix(in oklab, var(--primary) 50%, var(--card))'
const SPIKE_COLOR = 'var(--destructive)'
const INCOME_COLOR = LEFT_COLOR

interface MonthlyChartProps {
  months: OverviewMonth[]
  currency: string
  locale: string
}

export function MonthlyChart({ months, currency, locale }: MonthlyChartProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { mask, privacyMode } = usePrivacyMode()
  const [ref, width] = useElementWidth<HTMLDivElement>()
  const [active, setActive] = useState<number | null>(null)

  const median = Number(months[0]?.median_spending ?? 0)
  const values = months.map((m) => ({ ...m, s: Number(m.spending), i: Number(m.income) }))
  const max = Math.max(...values.map((v) => Math.max(v.s, v.i)), median, 1)
  const ticks = niceTicks(max, 4)
  const top = ticks[ticks.length - 1]
  const plotW = Math.max(10, width - PAD.left - PAD.right)
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const band = plotW / values.length
  const barW = Math.min(24, Math.max(4, band * 0.6))
  const y = (v: number) => PAD.top + plotH - (v / top) * plotH
  const cx = (i: number) => PAD.left + band * i + band / 2
  const labelEvery = band < 34 ? 2 : 1
  const money = (v: number) => mask(formatMoneyShort(v, currency, locale))
  const full = (v: number) => mask(formatCurrency(v, currency, locale))

  const peak = peakMonth(months)
  const aria = peak
    ? t(peak.spike ? 'reports.overview.monthly.ariaSpike' : 'reports.overview.monthly.aria', {
        month: monthName(peak.month.month, locale),
        value: money(Number(peak.month.spending)),
        multiple: peak.multiple?.toLocaleString(locale, { maximumFractionDigits: 1 }) ?? '',
        median: money(median),
      })
    : ''

  const incomePath = values
    .map((v, i) => `${i === 0 ? 'M' : 'L'}${cx(i).toFixed(1)},${y(v.i).toFixed(1)}`)
    .join(' ')
  const open = (month: string) => {
    const link = monthExploreLink(month)
    navigate(link.to, { state: link.state })
  }
  const activeValue = active !== null ? values[active] : null

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground" aria-hidden="true">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: BAR_COLOR }} />
          {t('reports.overview.monthly.spending')}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-3.5 rounded-full" style={{ background: INCOME_COLOR }} />
          {t('reports.overview.monthly.income')}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3.5 border-t border-dashed border-muted-foreground" />
          {t('reports.overview.monthly.median')}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: SPIKE_COLOR }} />
          {t('reports.overview.monthly.spike')}
        </span>
      </div>
      <div ref={ref} className="relative w-full" onMouseLeave={() => setActive(null)}>
        <svg
          width={width}
          height={HEIGHT}
          role="group"
          aria-label={aria}
          data-testid="monthly-chart"
          className="block overflow-visible"
        >
          {ticks.map((tick) => (
            <g key={tick} aria-hidden="true">
              <line x1={PAD.left} x2={width - PAD.right} y1={y(tick)} y2={y(tick)} stroke="var(--border)" strokeWidth={1} />
              <text x={PAD.left - 8} y={y(tick)} dy="0.32em" textAnchor="end" fontSize={10} fill="var(--muted-foreground)" className="tabular-nums">
                {privacyMode ? '' : tick === 0 ? '0' : formatMoneyShort(tick, currency, locale)}
              </text>
            </g>
          ))}
          {values.map((v, i) => {
            const spike = median > 0 && v.s >= SPIKE_MULTIPLE * median
            const h = Math.max(0, PAD.top + plotH - y(v.s))
            const x = cx(i) - barW / 2
            const r = Math.min(4, barW / 2, h)
            const label = t('reports.overview.monthly.barAria', {
              month: monthName(v.month, locale),
              spending: full(v.s),
              income: full(v.i),
            })
            return (
              <g
                key={v.month}
                role="button"
                tabIndex={0}
                aria-label={label}
                data-testid={`month-${v.month}`}
                className="cursor-pointer outline-none [&:focus-visible>rect:first-child]:fill-[var(--muted)]"
                onMouseEnter={() => setActive(i)}
                onFocus={() => setActive(i)}
                onBlur={() => setActive(null)}
                onClick={() => open(v.month)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    open(v.month)
                  }
                }}
              >
                {/* The whole column is the hit target, not just the bar. */}
                <rect
                  x={PAD.left + band * i}
                  y={PAD.top}
                  width={band}
                  height={plotH}
                  fill={active === i ? 'var(--muted)' : 'transparent'}
                  opacity={0.6}
                />
                {h > 0 && (
                  <path
                    d={`M${x},${y(0)} V${y(v.s) + r} Q${x},${y(v.s)} ${x + r},${y(v.s)} H${x + barW - r} Q${x + barW},${y(v.s)} ${x + barW},${y(v.s) + r} V${y(0)} Z`}
                    style={{ fill: spike ? SPIKE_COLOR : BAR_COLOR }}
                  />
                )}
                {spike && (
                  <text x={cx(i)} y={y(v.s) - 6} textAnchor="middle" fontSize={10} fontWeight={600} fill="var(--foreground)">
                    {`${(v.s / median).toLocaleString(locale, { maximumFractionDigits: 1 })}×`}
                  </text>
                )}
                {i % labelEvery === 0 && (
                  <text x={cx(i)} y={HEIGHT - 8} textAnchor="middle" fontSize={10} fill="var(--muted-foreground)">
                    {new Date(`${v.month}-01T00:00:00`).toLocaleDateString(locale, { month: 'short' }).replace('.', '')}
                  </text>
                )}
              </g>
            )
          })}
          {median > 0 && (
            <g aria-hidden="true" pointerEvents="none">
              <line
                x1={PAD.left}
                x2={width - PAD.right}
                y1={y(median)}
                y2={y(median)}
                stroke="var(--muted-foreground)"
                strokeWidth={1}
                strokeDasharray="4 3"
              />
            </g>
          )}
          <g aria-hidden="true" pointerEvents="none">
            <path d={incomePath} fill="none" style={{ stroke: INCOME_COLOR }} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            {values.map((v, i) => (
              <circle key={v.month} cx={cx(i)} cy={y(v.i)} r={active === i ? 5 : 3.5} style={{ fill: INCOME_COLOR }} stroke="var(--card)" strokeWidth={2} />
            ))}
          </g>
          <line x1={PAD.left} x2={width - PAD.right} y1={y(0)} y2={y(0)} stroke="var(--muted-foreground)" strokeOpacity={0.4} strokeWidth={1} aria-hidden="true" />
        </svg>
        {activeValue && active !== null && (
          <ChartTooltip x={cx(active)} y={Math.min(y(activeValue.s), y(activeValue.i))} width={width}>
            <p className="mb-1 font-semibold capitalize text-foreground">
              {new Date(`${activeValue.month}-01T00:00:00`).toLocaleDateString(locale, { month: 'long', year: 'numeric' })}
            </p>
            <TooltipRow color={BAR_COLOR} label={t('reports.overview.monthly.spending')} value={full(activeValue.s)} strong />
            <TooltipRow color={INCOME_COLOR} label={t('reports.overview.monthly.income')} value={full(activeValue.i)} />
            <TooltipRow
              label={t(activeValue.i - activeValue.s >= 0 ? 'reports.overview.monthly.left' : 'reports.overview.monthly.short')}
              value={full(Math.abs(activeValue.i - activeValue.s))}
            />
            {median > 0 && (
              <p className="mt-1 text-muted-foreground">
                {t('reports.overview.monthly.vsMedian', {
                  multiple: (activeValue.s / median).toLocaleString(locale, { maximumFractionDigits: 1 }),
                })}
              </p>
            )}
            <p className="mt-1 text-[11px] font-medium text-primary">{t('reports.overview.monthly.clickHint')}</p>
          </ChartTooltip>
        )}
      </div>
    </div>
  )
}
