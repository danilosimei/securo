/** The period picker of Reports. One look for every tab: the chosen shortcut
 *  (a menu of presets), an optional custom range next to it, and the real
 *  dates covered on a line below. `ReportPeriodSelector` is the closed-period flavour
 *  (Overview, Explore, Trends, Habits); Cash Flow and Net Worth pass their own
 *  options to `PeriodSegments`. */
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { CalendarDays, ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { localDateString } from '@/lib/date-utils'
import { DEFAULT_REPORT_PERIOD, REPORT_PERIODS, monthLabel, type ReportPeriodKey } from '@/lib/report-period'
import { DateRangePicker } from '@/components/ui/date-range-picker'

export const CUSTOM_RANGE_MAX_YEARS = 10

interface PeriodSegmentsProps<K extends string> {
  options: { key: K; label: string }[]
  value: K | null
  onSelect: (key: K) => void
  /** A trailing segment (the custom range picker). */
  custom?: ReactNode
  /** The dates the period covers, under the control. */
  rangeText?: string | null
}

/** A compact period control: the current shortcut opens a list of presets
 *  (rows, the chosen one checked), and the custom range sits next to it as a
 *  second segment. Narrow enough to share the row with the Reports tabs. */
export function PeriodSegments<K extends string>({ options, value, onSelect, custom, rangeText }: PeriodSegmentsProps<K>) {
  const { t } = useTranslation()
  const current = options.find((o) => o.key === value)
  return (
    <div className="space-y-1 sm:text-right">
      <div
        role="group"
        aria-label={t('reports.explore.periodGroup')}
        className="flex w-full items-center gap-0.5 rounded-lg border border-border bg-muted p-0.5 text-xs font-medium sm:inline-flex sm:w-auto"
      >
        <DropdownMenu>
          <DropdownMenuTrigger
            aria-label={t('reports.periodMenu', { period: current?.label ?? t('reports.explore.period.custom') })}
            data-testid="period-menu"
            className={cn(
              'flex flex-1 items-center justify-between gap-1.5 rounded-md px-3 py-1.5 whitespace-nowrap transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:flex-none',
              current ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <CalendarDays size={13} aria-hidden="true" className="text-muted-foreground" />
            <span>{current?.label ?? t('reports.periodPresets')}</span>
            <ChevronDown size={13} aria-hidden="true" className="text-muted-foreground" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-44">
            <DropdownMenuRadioGroup value={value ?? ''} onValueChange={(key) => onSelect(key as K)}>
              {options.map((option) => (
                <DropdownMenuRadioItem key={option.key} value={option.key}>
                  {option.label}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
        {custom}
      </div>
      {rangeText && <p className="text-xs text-muted-foreground">{rangeText}</p>}
    </div>
  )
}

interface ReportPeriodSelectorProps {
  period: ReportPeriodKey
  /** Custom range endpoints (YYYY-MM-DD), empty when none. */
  from: string
  to: string
  onChange: (period: ReportPeriodKey, range?: { from: string; to: string }) => void
  /** The range the period covers, shown under the buttons. */
  range?: { from: string; to: string; partial: boolean } | null
  locale: string
  /** Period applied when the custom picker is reset. */
  resetPeriod?: ReportPeriodKey
}

export function ReportPeriodSelector({
  period,
  from,
  to,
  onChange,
  range,
  locale,
  resetPeriod = DEFAULT_REPORT_PERIOD,
}: ReportPeriodSelectorProps) {
  const { t } = useTranslation()
  const today = localDateString()
  const rangeText = range
    ? t(range.partial ? 'reports.explore.rangePartial' : 'reports.explore.rangeClosed', {
        from: monthLabel(range.from, locale),
        to: monthLabel(range.to, locale),
      })
    : null

  return (
    <PeriodSegments
      options={REPORT_PERIODS.filter((p) => p !== 'custom').map((p) => ({ key: p, label: t(`reports.explore.period.${p}`) }))}
      value={period === 'custom' ? null : period}
      onSelect={(p) => onChange(p)}
      rangeText={rangeText}
      custom={
        <DateRangePicker
          variant="segment"
          className="rounded-md"
          active={period === 'custom'}
          from={from}
          to={to}
          defaultFrom={`${today.slice(0, 4)}-01-01`}
          defaultTo={today}
          disallowFuture
          maxRangeYears={CUSTOM_RANGE_MAX_YEARS}
          onChange={(f, e) => (f && e ? onChange('custom', { from: f, to: e }) : onChange(resetPeriod))}
          label={t('reports.explore.period.custom')}
          placeholder={t('reports.explore.period.custom')}
        />
      }
    />
  )
}
