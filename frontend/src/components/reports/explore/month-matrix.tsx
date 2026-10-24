/** "Mês": a table of colors, one row per group (or category), one column
 *  per month of the last 12. The darker the cell, the more was spent; the
 *  legend says so in words. A cell opens that group in that month; a name
 *  opens it over the whole period. On wide screens the value is written in
 *  the cell; on a phone, a tap or the tooltip reads it. */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { BreakdownRow } from '@/lib/api'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatMoneyShort, useIsNarrow } from '@/components/reports/common/chart-utils'
import { CategoryGlyph } from '@/components/reports/common/category-glyph'
import { HEAT_STEPS, heatStep, type Glyph } from './explore-model'

interface MonthMatrixProps {
  rows: BreakdownRow[]
  months: string[]
  currency: string
  locale: string
  /** A second line under a category's name (its group). */
  sublabel?: (row: BreakdownRow) => string | null | undefined
  onOpenCell: (row: BreakdownRow, month: string) => void
  onOpenRow: (row: BreakdownRow) => void
  ariaLabel: string
  /** The group's or category's icon before its name. */
  glyphOf?: (row: BreakdownRow) => Glyph
  /** Money received instead of spending: the legend says so. */
  income?: boolean
}

const heatColor = (step: number) => (step === 0 ? 'transparent' : `var(--heat-${step})`)
const heatInk = (step: number) => (step === 0 ? 'var(--muted-foreground)' : `var(--heat-ink-${step})`)

export function MonthMatrix({ rows, months, currency, locale, sublabel, onOpenCell, onOpenRow, ariaLabel, glyphOf, income }: MonthMatrixProps) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const narrow = useIsNarrow()
  const [hover, setHover] = useState<string | null>(null)
  const shown = rows.filter((r) => r.sparkline.some((v) => Number(v) > 0))
  const max = Math.max(...shown.flatMap((r) => r.sparkline.map(Number)), 0)
  // Cells are narrow: the number without the currency ("4,2 mil").
  const compact = new Intl.NumberFormat(locale, { notation: 'compact', maximumFractionDigits: 1 })
  const monthLabel = (m: string, style: 'narrow' | 'short') =>
    new Date(`${m}-01T00:00:00`).toLocaleDateString(locale, { month: style }).replace('.', '')
  const monthLong = (m: string) =>
    new Date(`${m}-01T00:00:00`).toLocaleDateString(locale, { month: 'long', year: 'numeric' })

  return (
    <div data-testid="month-matrix">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground" aria-hidden="true">
        <span>{t('reports.explore.month.less')}</span>
        <span className="flex gap-0.5">
          {Array.from({ length: HEAT_STEPS }, (_, i) => (
            <span key={i} className="size-3.5 rounded-[3px]" style={{ background: heatColor(i + 1) }} />
          ))}
        </span>
        <span>{t('reports.explore.month.more')}</span>
        {/* The ramp runs dark-to-light on a dark screen: the words follow it. */}
        <span className="ml-1 dark:hidden">{t(income ? 'reports.explore.income.month.legend' : 'reports.explore.month.legend')}</span>
        <span className="ml-1 hidden dark:inline">
          {t(income ? 'reports.explore.income.month.legendDark' : 'reports.explore.month.legendDark')}
        </span>
      </div>
      <table className="w-full table-fixed border-separate border-spacing-0.5 text-xs" aria-label={ariaLabel}>
        <thead>
          <tr>
            <th className="w-24 sm:w-44">
              <span className="sr-only">{t('reports.explore.col.group')}</span>
            </th>
            {months.map((m) => (
              <th key={m} scope="col" className="pb-1 text-center text-[10px] font-medium capitalize text-muted-foreground">
                <span aria-hidden="true">{monthLabel(m, narrow ? 'narrow' : 'short')}</span>
                <span className="sr-only">{monthLong(m)}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row) => {
            const sub = sublabel?.(row)
            const glyph = glyphOf?.(row)
            return (
              <tr key={row.id ?? row.label} data-testid="month-row">
                <th scope="row" className="pr-1 text-left font-normal">
                  <button
                    type="button"
                    onClick={() => onOpenRow(row)}
                    className="block w-full min-w-0 rounded text-left hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <span className="flex min-w-0 items-center gap-1.5">
                      {glyph && <CategoryGlyph icon={glyph.icon} color={glyph.color} size={12} />}
                      <span className="block truncate text-xs font-medium text-foreground">{row.label}</span>
                    </span>
                    {sub && <span className="hidden truncate text-[10px] text-muted-foreground sm:block">{sub}</span>}
                  </button>
                </th>
                {months.map((m, i) => {
                  const value = Number(row.sparkline[i] ?? 0)
                  const step = heatStep(value, max)
                  const key = `${row.id ?? row.label}:${m}`
                  const text = mask(formatMoneyShort(value, currency, locale))
                  return (
                    <td key={m} className="p-0">
                      <button
                        type="button"
                        data-testid="month-cell"
                        data-step={step}
                        disabled={value <= 0}
                        onClick={() => onOpenCell(row, m)}
                        onMouseEnter={() => setHover(key)}
                        onMouseLeave={() => setHover(null)}
                        onFocus={() => setHover(key)}
                        onBlur={() => setHover(null)}
                        aria-label={`${row.label}, ${monthLong(m)}: ${mask(formatCurrency(value, currency, locale))}`}
                        title={`${row.label} · ${monthLong(m)}: ${text}`}
                        className={cn(
                          'relative flex h-8 w-full items-center justify-center overflow-hidden rounded-[4px] text-[10px] font-medium tabular-nums transition-shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default',
                          step === 0 && 'bg-muted/40',
                          hover === key && value > 0 && 'ring-2 ring-foreground/60',
                        )}
                        style={{ background: step ? heatColor(step) : undefined, color: heatInk(step) }}
                      >
                        <span aria-hidden="true" className="hidden truncate px-0.5 xl:inline">
                          {value > 0 ? mask(compact.format(value)) : ''}
                        </span>
                      </button>
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
