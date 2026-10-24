/** The one place Reports shows its period: on the tab row, at the right on
 *  wide screens and under the tabs, full width, on a phone. The page renders
 *  it once; each tab only says which options it takes. */
import { useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { reportPeriodRange } from '@/lib/report-period'
import { ReportPeriodSelector } from '@/components/reports/period-selector'
import { useReportPeriod } from '@/components/reports/common/report-url'
import { ReportRangeContext, useReportRange, type ReportRange } from '@/components/reports/common/report-range'

export function ReportRangeProvider({ children }: { children: ReactNode }) {
  const [range, setRange] = useState<ReportRange | null>(null)
  const value = useMemo(() => ({ range, setRange }), [range])
  return <ReportRangeContext.Provider value={value}>{children}</ReportRangeContext.Provider>
}

/** The closed periods of Overview, Explore, Trends and Habits, kept in the URL. */
export function ClosedPeriodBar() {
  const locale = useDisplayLocale()
  const { period, from, to, update } = useReportPeriod()
  const reported = useReportRange()
  // Until the tab answers, the dates the period should cover.
  const range = reported ?? reportPeriodRange(period, new Date(), { from, to })
  return <ReportPeriodSelector period={period} from={from} to={to} onChange={update} locale={locale} range={range} />
}

/** Fixos reads a fixed window: no picker, the window said in its place. */
export function FixedWindowNote() {
  const { t } = useTranslation()
  return (
    <p
      data-testid="reports-fixed-window"
      className="flex h-[34px] items-center rounded-lg border border-border bg-muted px-3 text-xs font-medium text-muted-foreground sm:inline-flex"
    >
      {t('reports.recurring.fixedWindow')}
    </p>
  )
}
