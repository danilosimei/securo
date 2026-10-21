/** The closed-period picker shared by Reports tabs (Explore, Income & Expenses,
 *  Money Map): fixed shortcuts, a custom range, and the real dates covered. */
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { localDateString } from '@/lib/date-utils'
import { DEFAULT_REPORT_PERIOD, REPORT_PERIODS, monthLabel, type ReportPeriodKey } from '@/lib/report-period'
import { DateRangePicker } from '@/components/ui/date-range-picker'

export const CUSTOM_RANGE_MAX_YEARS = 10

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
    <div className="space-y-1.5">
      <div
        role="group"
        aria-label={t('reports.explore.periodGroup')}
        className="inline-flex max-w-full flex-wrap items-center gap-0.5 rounded-lg border border-border bg-muted p-0.5 text-xs font-medium"
      >
        {REPORT_PERIODS.filter((p) => p !== 'custom').map((p) => (
          <button
            key={p}
            type="button"
            aria-pressed={period === p}
            onClick={() => onChange(p)}
            className={cn(
              'rounded-md px-3 py-1.5 whitespace-nowrap transition-colors',
              period === p ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {t(`reports.explore.period.${p}`)}
          </button>
        ))}
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
      </div>
      {rangeText && <p className="text-xs text-muted-foreground">{rangeText}</p>}
    </div>
  )
}
