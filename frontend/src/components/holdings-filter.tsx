/** "Somar" on the Assets page: one chip per wallet and per asset outside a
 *  wallet, all on by default. Turning one off takes it out of the total, the
 *  chart and the list; a short line then says how much of the whole is being
 *  added up, with a way back to all of it. */
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { nonInvestmentKeys, type HoldingChip } from '@/lib/holdings-filter'

interface HoldingsFilterProps {
  chips: HoldingChip[]
  hidden: ReadonlySet<string>
  onChange: (hidden: string[]) => void
  iconOf: (chip: HoldingChip) => ReactNode
  /** The total of what is on, already formatted (and masked). */
  shownTotal: string
}

export function HoldingsFilter({ chips, hidden, onChange, iconOf, shownTotal }: HoldingsFilterProps) {
  const { t } = useTranslation()
  if (chips.length < 2) return null
  const off = chips.filter((c) => hidden.has(c.key)).map((c) => c.key)
  const nonInvestments = nonInvestmentKeys(chips)
  const toggle = (key: string) => onChange(off.includes(key) ? off.filter((k) => k !== key) : [...off, key])
  const linkClass =
    'font-medium text-primary hover:underline disabled:text-muted-foreground disabled:no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm'

  return (
    <div className="space-y-1.5" data-testid="holdings-filter">
      <div role="group" aria-label={t('assets.sumFilter.label')} className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs font-medium text-muted-foreground">{t('assets.sumFilter.label')}</span>
        {chips.map((chip) => {
          const on = !hidden.has(chip.key)
          return (
            <button
              key={chip.key}
              type="button"
              aria-pressed={on}
              onClick={() => toggle(chip.key)}
              data-testid="holdings-filter-chip"
              className={cn(
                'flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                on ? 'border-border bg-card text-foreground' : 'border-dashed border-border bg-transparent text-muted-foreground line-through',
              )}
            >
              <span className={cn('flex shrink-0', !on && 'opacity-50')}>{iconOf(chip)}</span>
              <span className="truncate">{chip.label}</span>
            </button>
          )
        })}
        <span className="ml-1 flex items-center gap-1.5 text-xs text-muted-foreground">
          <button type="button" className={linkClass} disabled={off.length === 0} onClick={() => onChange([])}>
            {t('assets.sumFilter.all')}
          </button>
          {nonInvestments.length > 0 && (
            <>
              <span aria-hidden="true">·</span>
              <button type="button" className={linkClass} onClick={() => onChange(nonInvestments)}>
                {t('assets.sumFilter.investmentsOnly')}
              </button>
            </>
          )}
        </span>
      </div>
      {off.length > 0 && (
        <p className="text-xs text-muted-foreground" data-testid="holdings-filter-summary">
          {t('assets.sumFilter.summary', { shown: chips.length - off.length, total: chips.length, value: shownTotal })}
          <span aria-hidden="true"> · </span>
          <button type="button" className={linkClass} onClick={() => onChange([])}>
            {t('assets.sumFilter.showAll')}
          </button>
        </p>
      )}
    </div>
  )
}
