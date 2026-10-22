/** "What changed": the groups that rose or fell most in the last closed month
 *  against the average of the three before it, as bars that diverge from a
 *  zero line. More spending goes right in rose, less goes left in green, and
 *  the sign is always written next to the value, so color is never alone. */
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import type { OverviewChange, ReportPeriodParams } from '@/lib/api'
import { cn } from '@/lib/utils'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatMoneyShort, formatPercent } from '@/components/reports/common/chart-utils'
import type { GroupPalette } from '@/components/reports/common/group-colors'
import { exploreLink } from '@/components/reports/common/report-url'

interface ChangesChartProps {
  changes: OverviewChange[]
  query: ReportPeriodParams
  currency: string
  locale: string
  palette: GroupPalette
}

export function ChangesChart({ changes, query, currency, locale, palette }: ChangesChartProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { mask } = usePrivacyMode()
  const rows = [...changes].sort((a, b) => Number(b.delta) - Number(a.delta))
  const max = Math.max(...rows.map((r) => Math.abs(Number(r.delta))), 1)

  const open = (row: OverviewChange) => {
    if (!row.id) return
    const group = row.kind === 'group' ? row.id : palette.groupOfCategory(row.id)
    const link = exploreLink(
      { ...query, group, category: row.kind === 'category' ? row.id : null },
      {
        group: group ? (palette.groupName(group) ?? (row.kind === 'group' ? row.label : undefined)) : undefined,
        category: row.kind === 'category' ? row.label : undefined,
      },
    )
    navigate(link.to, { state: link.state })
  }

  return (
    <ul className="space-y-1" data-testid="changes-chart">
      {rows.map((row) => {
        const delta = Number(row.delta)
        const up = delta > 0
        const width = `${(Math.abs(delta) / max) * 50}%`
        const value = `${up ? '+' : '−'}${mask(formatMoneyShort(Math.abs(delta), currency, locale))}`
        const pct = row.change === null ? null : formatPercent(row.change, locale, true)
        const label = t('reports.overview.changes.row', {
          name: row.label,
          value,
          change: pct ?? '—',
        })
        return (
          <li key={`${row.kind}:${row.id ?? row.label}`}>
            <button
              type="button"
              disabled={!row.id}
              onClick={() => open(row)}
              aria-label={label}
              className="grid w-full grid-cols-[minmax(0,7.5rem)_1fr_minmax(0,6.5rem)] items-center gap-2 rounded-md px-1.5 py-1.5 text-left text-sm transition-colors hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default disabled:hover:bg-transparent sm:grid-cols-[minmax(0,10rem)_1fr_minmax(0,7.5rem)]"
            >
              <span className="truncate font-medium text-foreground">{row.label}</span>
              <span className="relative h-3" aria-hidden="true">
                <span className="absolute inset-y-[-4px] left-1/2 w-px bg-border" />
                <span
                  className={cn(
                    'absolute top-0 h-3',
                    up ? 'left-1/2 rounded-r bg-rose-500/85 dark:bg-rose-400/85' : 'right-1/2 rounded-l bg-emerald-500/85 dark:bg-emerald-400/85',
                  )}
                  style={{ width }}
                />
              </span>
              <span className="text-right tabular-nums">
                <span className="font-semibold text-foreground">{value}</span>
                {pct && <span className="ml-1 text-xs text-muted-foreground">{pct}</span>}
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}
