/** Two small bar charts of the Habits tab: spending by weekday (7 columns,
 *  the heaviest day in the strong color) and purchases by value band (each
 *  band with its count and its total side by side, so "many small ones" and
 *  "a few big ones" read at a glance). Every value is written on the chart. */
import { useTranslation } from 'react-i18next'
import type { HabitsBand, HabitsWeekday } from '@/lib/api'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatMoneyShort } from '@/components/reports/common/chart-utils'
import { bandLabel, weekdayName } from './habits-model'

const SOFT = 'color-mix(in oklab, var(--primary) 45%, var(--card))'

export function WeekdayBars({ weekdays, currency, locale }: { weekdays: HabitsWeekday[]; currency: string; locale: string }) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const days = [...weekdays].sort((a, b) => a.weekday - b.weekday)
  const max = Math.max(...days.map((d) => Number(d.total)), 1)
  const top = days.reduce((best, d) => (Number(d.total) > Number(best.total) ? d : best), days[0])
  return (
    <ol className="grid h-52 grid-cols-7 items-end gap-1.5 sm:gap-3" data-testid="weekday-bars">
      {days.map((d) => {
        const value = Number(d.total)
        const strong = d === top
        return (
          <li
            key={d.weekday}
            data-testid="weekday-bar"
            aria-label={t('reports.habits.weekdays.aria', {
              day: weekdayName(d.weekday, locale, 'long'),
              total: mask(formatCurrency(value, currency, locale)),
              n: d.count,
            })}
            className="flex h-full min-w-0 flex-col items-center justify-end gap-1"
          >
            <span className={cn('truncate text-[10px] tabular-nums sm:text-[11px]', strong ? 'font-semibold text-foreground' : 'text-muted-foreground')}>
              {mask(formatMoneyShort(value, currency, locale))}
            </span>
            <span
              aria-hidden="true"
              className="w-full max-w-6 rounded-t"
              style={{ height: `${Math.max(2, (value / max) * 100)}%`, background: strong ? 'var(--primary)' : SOFT }}
            />
            <span className={cn('text-xs capitalize', strong ? 'font-semibold text-foreground' : 'text-muted-foreground')}>
              {weekdayName(d.weekday, locale)}
            </span>
            <span className="text-[10px] tabular-nums text-muted-foreground">{d.count}</span>
          </li>
        )
      })}
    </ol>
  )
}

export function BandBars({ bands, currency, locale }: { bands: HabitsBand[]; currency: string; locale: string }) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const maxCount = Math.max(...bands.map((b) => b.count), 1)
  const maxTotal = Math.max(...bands.map((b) => Number(b.total)), 1)
  return (
    <div data-testid="band-bars">
      <div className="mb-1.5 grid grid-cols-[7.5rem_1fr_1fr] gap-3 text-[11px] font-medium text-muted-foreground sm:grid-cols-[8rem_1fr_1fr]">
        <span>{t('reports.habits.bands.band')}</span>
        <span>{t('reports.habits.bands.count')}</span>
        <span>{t('reports.habits.bands.total')}</span>
      </div>
      <ol className="space-y-2">
        {bands.map((band) => {
          const label = bandLabel(band, currency, locale, t)
          const total = Number(band.total)
          return (
            <li
              key={`${band.min}-${band.max}`}
              data-testid="band-bar"
              aria-label={t('reports.habits.bands.aria', {
                band: label,
                n: band.count,
                total: mask(formatCurrency(total, currency, locale)),
              })}
              className="grid grid-cols-[7.5rem_1fr_1fr] items-center gap-3 text-sm sm:grid-cols-[8rem_1fr_1fr]"
            >
              <span className="truncate text-xs font-medium text-foreground">{label}</span>
              <span className="flex min-w-0 items-center gap-1.5">
                <span aria-hidden="true" className="h-2.5 shrink-0 rounded-r" style={{ width: `${(band.count / maxCount) * 55}%`, background: SOFT }} />
                <span className="text-xs tabular-nums text-foreground">{band.count.toLocaleString(locale)}</span>
              </span>
              <span className="flex min-w-0 items-center gap-1.5">
                <span aria-hidden="true" className="h-2.5 shrink-0 rounded-r bg-primary" style={{ width: `${(total / maxTotal) * 45}%` }} />
                <span className="shrink-0 whitespace-nowrap text-xs tabular-nums text-foreground">{mask(formatMoneyShort(total, currency, locale))}</span>
              </span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
