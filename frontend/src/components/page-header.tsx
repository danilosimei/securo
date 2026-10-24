// frontend/src/components/page-header.tsx
import { ArrowLeft } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { PeriodSlot } from '@/components/reports/period-selector'

/** Where "Back" goes: the list the page belongs to, and that list's name. */
export interface PageBack {
  to: string
  /** Name of the list, as in "Back to Accounts". */
  label: string
  testId?: string
}

/** The "← Back to Accounts" link, the same on every detail page. */
export function BackLink({ to, label, testId }: PageBack) {
  const { t } = useTranslation()
  return (
    <Link
      to={to}
      data-testid={testId}
      className="mb-2 inline-flex items-center gap-1 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
    >
      <ArrowLeft className="h-3.5 w-3.5" />
      {t('common.backTo', { name: label })}
    </Link>
  )
}

interface PageHeaderProps {
  /** The menu group the page sits in; left out for pages with none. */
  section?: string
  title: string
  action?: React.ReactNode
  /** The page's period control. It sits where Reports puts its own: at the
   *  right of the title on a wide screen, under it (full width on a phone)
   *  otherwise. Can sit next to `action`. */
  period?: React.ReactNode
  /** Detail pages: the "← Back to X" link above the title. */
  back?: PageBack
}

export function PageHeader({ section, title, action, period, back }: PageHeaderProps) {
  const heading = (
    <div>
      {back && <BackLink {...back} />}
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
