// frontend/src/components/page-header.tsx
import { PeriodSlot } from '@/components/reports/period-selector'

interface PageHeaderProps {
  /** The menu group the page sits in; left out for pages with none. */
  section?: string
  title: string
  action?: React.ReactNode
  /** The page's period control. It sits where Reports puts its own: at the
   *  right of the title on a wide screen, under it (full width on a phone)
   *  otherwise. Can sit next to `action`. */
  period?: React.ReactNode
}

export function PageHeader({ section, title, action, period }: PageHeaderProps) {
  const heading = (
    <div>
      {section && (
        <p className="text-xs font-medium text-muted-foreground mb-0.5">
          {section}
        </p>
      )}
      <h1 className="text-2xl font-semibold text-foreground tracking-tight">{title}</h1>
    </div>
  )
  if (period) {
    const slot = <PeriodSlot testId="page-period">{period}</PeriodSlot>
    return (
      <div className="mb-6 flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between lg:gap-6">
        {heading}
        {action ? (
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-end lg:gap-4">
            {action}
            {slot}
          </div>
        ) : (
          slot
        )}
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
