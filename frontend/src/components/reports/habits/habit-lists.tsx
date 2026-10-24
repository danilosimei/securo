/** A ranked list of stores with a plain bar each: where you buy most often
 *  ("8 times a month") or where each purchase costs most ("R$ 320 a
 *  purchase"). The name opens the store in Explore. Every value is written
 *  next to its bar, so nothing depends on reading the bar. */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { HabitsMerchant } from '@/lib/api'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatMoneyShort } from '@/components/reports/common/chart-utils'
import { timesPerMonth } from '@/components/reports/common/times-per-month'
import { habitRanking } from './habits-model'

/** Rows shown at first; the rest behind "show all". */
const HABIT_LIST_FIRST = 5

interface HabitListProps {
  merchants: HabitsMerchant[]
  /** `often`: purchases per month; `pricey`: value per purchase. */
  measure: 'often' | 'pricey'
  currency: string
  locale: string
  colorOf: (m: HabitsMerchant) => string
  onOpen: (m: HabitsMerchant) => void
  testId: string
}

export function HabitList({ merchants, measure, currency, locale, colorOf, onOpen, testId }: HabitListProps) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const [all, setAll] = useState(false)
  const ranked = habitRanking(merchants, measure)
  const rows = all ? ranked : ranked.slice(0, HABIT_LIST_FIRST)
  const valueOf = (m: HabitsMerchant) => (measure === 'often' ? m.per_month : Number(m.avg_ticket))
  const max = Math.max(...rows.map(valueOf), 0) || 1
  const times = (m: HabitsMerchant) => timesPerMonth(t, m.per_month, locale)
  const perPurchase = (m: HabitsMerchant) =>
    t('reports.habits.perPurchase', { value: mask(formatMoneyShort(Number(m.avg_ticket), currency, locale)) })
  return (
    <div>
      <ol className="space-y-1" data-testid={testId}>
        {rows.map((m) => {
          const main = measure === 'often' ? times(m) : perPurchase(m)
          const side = measure === 'often' ? perPurchase(m) : times(m)
          return (
            <li key={m.id}>
              <button
                type="button"
                onClick={() => onOpen(m)}
                aria-label={`${m.label}: ${main}, ${side}. ${t('reports.habits.openHint')}`}
                className="group w-full rounded-md px-2 py-1.5 text-left transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate font-medium text-foreground group-hover:underline group-hover:decoration-foreground/30 group-hover:underline-offset-4">
                    {m.label}
                  </span>
                  <span className="shrink-0 font-semibold tabular-nums text-foreground">{main}</span>
                </span>
                <span className="mt-1 flex items-center gap-3">
                  <span className="h-1.5 flex-1 rounded-full bg-muted" aria-hidden="true">
                    <span
                      className="block h-1.5 rounded-full"
                      style={{ width: `${Math.max(3, (valueOf(m) / max) * 100)}%`, background: colorOf(m) }}
                    />
                  </span>
                  <span className="w-28 shrink-0 truncate text-right text-[11px] tabular-nums text-muted-foreground">{side}</span>
                </span>
              </button>
            </li>
          )
        })}
      </ol>
      {ranked.length > HABIT_LIST_FIRST && (
        <button
          type="button"
          onClick={() => setAll((v) => !v)}
          className="mt-2 rounded px-2 text-xs font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {all ? t('reports.explore.bars.fewer') : t('reports.explore.bars.all', { n: ranked.length })}
        </button>
      )}
    </div>
  )
}
