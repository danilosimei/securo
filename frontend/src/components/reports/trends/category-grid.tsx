/** Small multiples: one mini chart per category, the biggest rise first. Each
 *  card says how much the category costs a month now and how that compares
 *  with the 3 months before; the last 3 months are shaded on the chart so the
 *  comparison is visible. A click opens the category in Explore. */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowDownRight, ArrowUpRight, Sparkles } from 'lucide-react'
import { cn } from '@/lib/utils'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatMoneyShort, formatPercent } from '@/components/reports/common/chart-utils'
import type { CategoryTrend } from './trends-model'

const W = 120
const H = 36
const INITIAL = 12

function Mini({ values, color }: { values: number[]; color: string }) {
  const max = Math.max(...values, 1)
  const n = values.length
  const x = (i: number) => (n <= 1 ? W / 2 : (i / (n - 1)) * W)
  const y = (v: number) => H - 2 - (Math.max(0, v) / max) * (H - 4)
  const line = values.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const area = `${line} L${x(n - 1)},${H} L${x(0)},${H} Z`
  const recentFrom = x(Math.max(0, n - 3)) - (n > 1 ? W / (n - 1) / 2 : 0)
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-9 w-full" aria-hidden="true">
      <rect x={recentFrom} y={0} width={W - recentFrom} height={H} fill="var(--muted)" opacity={0.7} />
      <path d={area} style={{ fill: color }} fillOpacity={0.14} />
      <path d={line} fill="none" style={{ stroke: color }} strokeWidth={1.75} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  )
}

interface CategoryGridProps {
  trends: CategoryTrend[]
  currency: string
  locale: string
  colorOf: (trend: CategoryTrend) => string
  onOpen: (trend: CategoryTrend) => void
}

export function CategoryGrid({ trends, currency, locale, colorOf, onOpen }: CategoryGridProps) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const [all, setAll] = useState(false)
  const shown = all ? trends : trends.filter((tr) => tr.material).slice(0, INITIAL)
  const hiddenCount = trends.length - shown.length

  return (
    <div>
      <ul className="grid grid-cols-1 gap-2.5 min-[420px]:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4" data-testid="trends-grid">
        {shown.map((tr) => {
          const up = (tr.change ?? 0) > 0
          const flat = tr.change !== null && Math.abs(tr.change) < 0.005
          const change = tr.isNew
            ? t('reports.trends.new')
            : tr.change === null
              ? '—'
              : formatPercent(tr.change, locale, true)
          const perMonth = t('reports.trends.perMonth', { value: mask(formatMoneyShort(tr.perMonth, currency, locale)) })
          return (
            <li key={tr.row.id ?? tr.row.label}>
              <button
                type="button"
                onClick={() => onOpen(tr)}
                data-testid="trends-card"
                aria-label={t('reports.trends.cardAria', { name: tr.row.label, value: perMonth, change })}
                className="flex w-full flex-col gap-1 rounded-lg border border-border bg-card p-3 text-left transition-colors hover:border-primary/40 hover:bg-primary/[0.03] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="flex items-start justify-between gap-2">
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-semibold text-foreground">{tr.row.label}</span>
                    {tr.row.group_label && <span className="block truncate text-[11px] text-muted-foreground">{tr.row.group_label}</span>}
                  </span>
                  <span
                    className={cn(
                      'inline-flex shrink-0 items-center gap-0.5 text-xs font-semibold tabular-nums',
                      tr.isNew
                        ? 'text-primary'
                        : tr.change === null || flat
                          ? 'text-muted-foreground'
                          : up
                            ? 'text-rose-600 dark:text-rose-400'
                            : 'text-emerald-600 dark:text-emerald-400',
                    )}
                  >
                    {tr.isNew ? (
                      <Sparkles size={12} aria-hidden="true" />
                    ) : tr.change !== null && !flat ? (
                      up ? <ArrowUpRight size={13} aria-hidden="true" /> : <ArrowDownRight size={13} aria-hidden="true" />
                    ) : null}
                    {change}
                  </span>
                </span>
                <Mini values={tr.values} color={colorOf(tr)} />
                <span className="text-xs text-muted-foreground">{perMonth}</span>
              </button>
            </li>
          )
        })}
      </ul>
      {(hiddenCount > 0 || all) && (
        <button type="button" onClick={() => setAll((v) => !v)} className="mt-3 text-xs font-semibold text-primary hover:underline">
          {all ? t('reports.trends.showFewer') : t('reports.trends.showAll', { n: trends.length })}
        </button>
      )}
    </div>
  )
}
