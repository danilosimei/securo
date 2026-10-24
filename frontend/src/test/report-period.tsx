/** A Reports tab under the page's period bar, as the page lays them out. */
import type { ReactNode } from 'react'
import { ClosedPeriodBar, ReportRangeProvider } from '@/components/reports/period-bar'

export function WithPeriodBar({ children }: { children: ReactNode }) {
  return (
    <ReportRangeProvider>
      <ClosedPeriodBar />
      {children}
    </ReportRangeProvider>
  )
}
