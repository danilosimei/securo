/** The money map on a phone: three bars on one scale (income, spending, and
 *  what was left or missing), each split by its parts. Tapping the spending
 *  bar lists its groups, and each group opens in Explore. */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatMoneyShort, formatPercent } from '@/components/reports/common/chart-utils'
import { LEFT_COLOR, NEUTRAL_COLOR, SHORT_COLOR, type GroupPalette } from '@/components/reports/common/group-colors'
import type { MapGroup, MapModel } from './money-map-model'

interface BarsProps {
  model: MapModel
  palette: GroupPalette
  currency: string
  locale: string
  onOpenGroup: (group: MapGroup) => void
  onExpand: () => void
}

function Segments({ parts, scale }: { parts: { key: string; value: number; color: string }[]; scale: number }) {
  return (
    <div className="flex h-3 w-full gap-[2px]" aria-hidden="true">
      {parts.map((p) => (
        <span
          key={p.key}
          className="h-full first:rounded-l last:rounded-r"
          style={{ width: `${(p.value / scale) * 100}%`, background: p.color, minWidth: p.value > 0 ? 2 : 0 }}
        />
      ))}
    </div>
  )
}

export function MoneyMapBars({ model, palette, currency, locale, onOpenGroup, onExpand }: BarsProps) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const [open, setOpen] = useState(false)
  const scale = Math.max(model.totalIncome, model.totalSpending, 1)
  const short = model.left < 0
  const money = (v: number) => mask(formatMoneyShort(v, currency, locale))
  const groups = [
    ...model.groups.map((g) => ({ key: g.id ?? g.label, value: g.value, color: palette.group(g.id, g.label) })),
    ...(model.folded ? [{ key: 'folded', value: model.folded.value, color: NEUTRAL_COLOR }] : []),
  ]

  return (
    <div className="space-y-4" data-testid="money-map-bars">
      <div>
        <div className="mb-1 flex items-baseline justify-between text-sm">
          <span className="font-medium text-foreground">{t('reports.moneyMap.income')}</span>
          <span className="tabular-nums font-semibold text-foreground">{money(model.totalIncome)}</span>
        </div>
        <Segments
          scale={scale}
          parts={model.income.map((i) => {
            const color = palette.group(i.id, i.label)
            return { key: i.id ?? i.label, value: i.value, color: color === NEUTRAL_COLOR ? LEFT_COLOR : color }
          })}
        />
      </div>

      <div>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="w-full rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="mb-1 flex items-baseline justify-between text-sm">
            <span className="flex items-center gap-1 font-medium text-foreground">
              {t('reports.moneyMap.spending')}
              {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
            </span>
            <span className="tabular-nums font-semibold text-foreground">{money(model.totalSpending)}</span>
          </span>
          <Segments scale={scale} parts={groups} />
          <span className="mt-1 block text-[11px] text-muted-foreground">{t('reports.moneyMap.tapGroups')}</span>
        </button>
        {open && (
          <ul className="mt-2 divide-y divide-border rounded-lg border border-border">
            {model.groups.map((g) => (
              <li key={g.id ?? g.label}>
                <button
                  type="button"
                  onClick={() => onOpenGroup(g)}
                  className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
                >
                  <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: palette.group(g.id, g.label) }} />
                  <span className="min-w-0 flex-1 truncate font-medium text-foreground">{g.label}</span>
                  <span className="tabular-nums text-foreground">{mask(formatCurrency(g.value, currency, locale))}</span>
                  <span className="w-10 text-right text-xs tabular-nums text-muted-foreground">
                    {formatPercent(model.totalSpending > 0 ? g.value / model.totalSpending : 0, locale)}
                  </span>
                </button>
              </li>
            ))}
            {model.folded && (
              <li>
                <button
                  type="button"
                  onClick={onExpand}
                  className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm text-muted-foreground hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
                >
                  <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: NEUTRAL_COLOR }} />
                  <span className="min-w-0 flex-1 truncate">
                    {t('reports.moneyMap.folded', { n: model.folded.groups.length })}
                  </span>
                  <span className="tabular-nums">{mask(formatCurrency(model.folded.value, currency, locale))}</span>
                </button>
              </li>
            )}
          </ul>
        )}
      </div>

      {model.left !== 0 && (
        <div>
          <div className="mb-1 flex items-baseline justify-between text-sm">
            <span className={cn('font-medium', short ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-400')}>
              {t(short ? 'reports.moneyMap.short' : 'reports.moneyMap.left')}
            </span>
            <span className="tabular-nums font-semibold text-foreground">{money(Math.abs(model.left))}</span>
          </div>
          <Segments scale={scale} parts={[{ key: 'left', value: Math.abs(model.left), color: short ? SHORT_COLOR : LEFT_COLOR }]} />
        </div>
      )}
    </div>
  )
}
