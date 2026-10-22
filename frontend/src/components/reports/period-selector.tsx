/** The period picker of Reports, also used by the Dashboard, Budgets and
 *  Transactions. One look everywhere: the chosen shortcut (a menu of presets),
 *  an optional custom range next to it, and the real dates covered on a line
 *  below. `ReportPeriodSelector` is the closed-period flavour (Overview,
 *  Explore, Trends, Habits); Cash Flow and Net Worth pass their own options to
 *  `PeriodSegments`; `MonthPeriodSelector` is the one-month flavour, with
 *  arrows to the month before and after. `PeriodSlot` is where any of them
 *  sits on a page. */
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { CalendarDays, ChevronDown, ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { MonthPicker } from '@/components/ui/monthpicker'
import { resolveDateFnsLocale } from '@/lib/date-fns-locale'
import { monthLabel as fullMonthLabel, shiftMonth } from '@/lib/month-utils'
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

/** Where the period sits: at the right of the title (or tab) row on a wide
 *  screen, under it on smaller ones, full width on a phone. The row around it
 *  goes from a column to a line at `lg` (see `PageHeader`'s `period`). */
export function PeriodSlot({ testId, children }: { testId: string; children: ReactNode }) {
  return (
    <div data-testid={testId} className="w-full shrink-0 sm:ml-auto sm:w-auto">
      {children}
    </div>
  )
}

/** The rounded frame the period controls share (same height and border). */
const GROUP_CLASS =
  'flex w-full items-center gap-0.5 rounded-lg border border-border bg-muted p-0.5 text-xs font-medium sm:inline-flex sm:w-auto'
/** The selected segment: the menu trigger of a period. */
const TRIGGER_CLASS =
  'flex flex-1 items-center justify-between gap-1.5 rounded-md px-3 py-1.5 whitespace-nowrap transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:flex-none'

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
      <div role="group" aria-label={t('reports.explore.periodGroup')} className={GROUP_CLASS}>
        <DropdownMenu>
          <DropdownMenuTrigger
            aria-label={t('reports.periodMenu', { period: current?.label ?? t('reports.explore.period.custom') })}
            data-testid="period-menu"
            className={cn(
              TRIGGER_CLASS,
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

interface MonthPeriodSelectorProps {
  /** Selected month as `"YYYY-MM"`. */
  value: string
  /** Called with the new `"YYYY-MM"`. The parent keeps the state. */
  onChange: (yearMonth: string) => void
  /** BCP-47 locale for the month name (e.g. "pt-BR"). */
  locale: string
  /** Show "10/2026" instead of the month name on a phone, for tight rows. */
  compactOnPhone?: boolean
}

/** One month at a time: the arrows step to the month before and after, the
 *  month itself opens a month picker. Same frame, height and trigger as the
 *  Reports period. */
export function MonthPeriodSelector({ value, onChange, locale, compactOnPhone = false }: MonthPeriodSelectorProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const label = fullMonthLabel(value, locale).replace(/^\w/, (c) => c.toUpperCase())
  const [year, month] = value.split('-').map(Number)
  const compactLabel = new Date(year, month - 1, 2).toLocaleDateString(locale, { month: '2-digit', year: 'numeric' })
  const arrow =
    'flex shrink-0 items-center justify-center rounded-md px-2 py-1.5 text-muted-foreground transition-colors hover:bg-card/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'

  return (
    <div role="group" aria-label={t('reports.explore.periodGroup')} data-testid="period-month" className={GROUP_CLASS}>
      <button type="button" aria-label={t('common.previousMonth')} className={arrow} onClick={() => onChange(shiftMonth(value, -1))}>
        <ChevronLeft size={14} aria-hidden="true" />
      </button>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label={t('reports.periodMenu', { period: label })}
            title={label}
            data-testid="period-menu"
            className={cn(TRIGGER_CLASS, 'min-w-0 bg-card text-foreground shadow-sm sm:min-w-40')}
          >
            <CalendarDays size={13} aria-hidden="true" className="shrink-0 text-muted-foreground" />
            <span className="truncate">
              {compactOnPhone ? (
                <>
                  <span className="sm:hidden">{compactLabel}</span>
                  <span className="hidden sm:inline">{label}</span>
                </>
              ) : (
                label
              )}
            </span>
            <ChevronDown size={13} aria-hidden="true" className="shrink-0 text-muted-foreground" />
          </button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-auto p-0">
          <MonthPicker
            locale={resolveDateFnsLocale(locale)}
            selectedMonth={new Date(`${value}-01T00:00:00`)}
            onMonthSelect={(date) => {
              if (!date) return
              onChange(`${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`)
              setOpen(false)
            }}
          />
        </PopoverContent>
      </Popover>
      <button type="button" aria-label={t('common.nextMonth')} className={arrow} onClick={() => onChange(shiftMonth(value, 1))}>
        <ChevronRight size={14} aria-hidden="true" />
      </button>
    </div>
  )
}

interface YearPeriodSelectorProps {
  /** Selected year, or `null` for every year. */
  value: number | null
  /** Years worth offering (those that have something); the selected one is always listed. */
  years: number[]
  onChange: (year: number | null) => void
  /** Text of one year ("Fiscal year 2026"). */
  yearLabel: (year: number) => string
  allLabel: string
}

/** One year at a time, or every year: the same frame and menu as the Reports
 *  period. A menu rather than arrows, because only years that have something
 *  are worth stepping to. */
export function YearPeriodSelector({ value, years, onChange, yearLabel, allLabel }: YearPeriodSelectorProps) {
  const list = Array.from(new Set(value ? [...years, value] : years)).sort((a, b) => b - a)
  return (
    <PeriodSegments
      options={[{ key: 'all', label: allLabel }, ...list.map((y) => ({ key: String(y), label: yearLabel(y) }))]}
      value={value ? String(value) : 'all'}
      onSelect={(key) => onChange(key === 'all' ? null : Number(key))}
    />
  )
}
