/** The dates the open Reports tab actually covers, as its server said (a
 *  fiscal-year "year to date" or a partial month included). The tab reports
 *  them; the period bar of the page shows them under the picker. */
import { createContext, useContext, useEffect } from 'react'

export interface ReportRange {
  from: string
  to: string
  partial: boolean
}

export const ReportRangeContext = createContext<{
  range: ReportRange | null
  setRange: (range: ReportRange | null) => void
} | null>(null)

/** Tabs call this with the period of their latest response. */
export function useReportedRange(range: ReportRange | null | undefined) {
  const setRange = useContext(ReportRangeContext)?.setRange
  const from = range?.from
  const to = range?.to
  const partial = range?.partial
  useEffect(() => {
    if (!setRange) return
    setRange(from && to ? { from, to, partial: !!partial } : null)
  }, [setRange, from, to, partial])
  useEffect(() => () => setRange?.(null), [setRange])
}

export function useReportRange() {
  return useContext(ReportRangeContext)?.range ?? null
}
