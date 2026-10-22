// frontend/src/components/page-header.tsx
import { PeriodSlot } from '@/components/reports/period-selector'

interface PageHeaderProps {
  section: string
  title: string
  action?: React.ReactNode
  /** The page's period control. It sits where Reports puts its own: at the
   *  right of the title on a wide screen, under it (full width on a phone)
   *  otherwise. Takes the place of `action`. */
  period?: React.ReactNode
}

export function PageHeader({ section, title, action, period }: PageHeaderProps) {
  const heading = (
    <div>
      <p className="text-xs font-medium text-muted-foreground mb-0.5">
        {section}
      </p>
      <h1 className="text-2xl font-semibold text-foreground tracking-tight">{title}</h1>
    </div>
  )
  if (period) {
    return (
      <div className="mb-6 flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between lg:gap-6">
        {heading}
        <PeriodSlot testId="page-period">{period}</PeriodSlot>
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between mb-6">
      {heading}
      {action}
    </div>
  )
}
