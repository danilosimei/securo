/** The current Explore list as horizontal bars, biggest first: name, a bar
 *  as long as what it cost, and the value and share written at its end. The
 *  first rows show at once, the rest behind "show all"; rows that take money
 *  off (refunds) stay out of the bars (the table lists them). A click goes
 *  down a level, like the row in the table. */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { BreakdownRow } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatMoneyShort, formatPercent } from '@/components/reports/common/chart-utils'
import { CategoryIcon } from '@/components/category-icon'

export const BARS_FIRST = 10

interface ExploreBarsProps {
  rows: BreakdownRow[]
  currency: string
  locale: string
  colorOf: (row: BreakdownRow) => string
  onOpen: (row: BreakdownRow) => void
  /** A second line under the name (the store's category). */
  sublabel?: (row: BreakdownRow) => string | null | undefined
  /** The group's or category's icon before the name (stores have none). */
  iconOf?: (row: BreakdownRow) => string | null
  ariaLabel: string
}

export function ExploreBars({ rows, currency, locale, colorOf, onOpen, sublabel, iconOf, ariaLabel }: ExploreBarsProps) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const [all, setAll] = useState(false)
  const positive = rows.filter((r) => Number(r.total) > 0)
  if (positive.length === 0) return null
  const total = positive.reduce((s, r) => s + Number(r.total), 0)
  const max = Number(positive[0].total)
  const shown = all ? positive : positive.slice(0, BARS_FIRST)

  return (
    <div data-testid="explore-bars">
      <ol className="space-y-1" aria-label={ariaLabel}>
        {shown.map((row) => {
          const value = Number(row.total)
          const share = formatPercent(value / total, locale)
          const sub = sublabel?.(row)
          return (
            <li key={row.id ?? row.label}>
              <button
                type="button"
                onClick={() => onOpen(row)}
                data-testid="explore-bar"
                aria-label={`${row.label}: ${mask(formatCurrency(value, currency, locale))} (${share})`}
                className="group grid w-full grid-cols-[minmax(0,9rem)_1fr] items-center gap-3 rounded-md px-1.5 py-1.5 text-left transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:grid-cols-[minmax(0,14rem)_1fr]"
              >
                <span className="flex min-w-0 items-center gap-2">
                  {iconOf && <CategoryIcon icon={iconOf(row)} color={colorOf(row)} size="xs" />}
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium text-foreground group-hover:underline group-hover:decoration-foreground/30 group-hover:underline-offset-4">
                      {row.label}
                    </span>
                    {sub && <span className="block truncate text-[11px] text-muted-foreground">{sub}</span>}
                  </span>
                </span>
                <span className="flex min-w-0 items-center gap-2">
                  <span
                    aria-hidden="true"
                    className="h-3 shrink-0 rounded-r-[4px]"
                    style={{ width: `max(3px, calc((100% - 6.5rem) * ${value / max}))`, background: colorOf(row) }}
                  />
                  <span className="shrink-0 whitespace-nowrap text-xs tabular-nums">
                    <span className="font-semibold text-foreground">{mask(formatMoneyShort(value, currency, locale))}</span>
                    <span className="ml-1.5 text-muted-foreground">{share}</span>
                  </span>
                </span>
              </button>
            </li>
          )
        })}
      </ol>
      {positive.length > BARS_FIRST && (
        <button
          type="button"
          onClick={() => setAll((v) => !v)}
          className="mt-2 rounded px-1.5 text-xs font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {all ? t('reports.explore.bars.fewer') : t('reports.explore.bars.all', { n: positive.length })}
        </button>
      )}
    </div>
  )
}
