import { cn } from '@/lib/utils'

/** The card every section of a page sits in. */
export function SectionCard({
  children,
  className = '',
}: {
  children: React.ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'bg-card rounded-xl border border-border shadow-sm overflow-hidden',
        className,
      )}
    >
      {children}
    </div>
  )
}

export function SectionHeader({
  title,
  titleExtra,
  description,
  action,
}: {
  title: string
  /** Small control next to the title (collapse all, say). */
  titleExtra?: React.ReactNode
  /** One line under the title, for a section whose rule is not obvious
   *  from its rows (what a "term" is, say). */
  description?: string
  action?: React.ReactNode
}) {
  return (
    <div className="px-4 sm:px-5 py-4 border-b border-border flex flex-wrap items-center justify-between gap-2">
      <div className="min-w-0">
        <div className="flex items-center gap-3">
          <p className="text-sm font-semibold text-foreground">{title}</p>
          {titleExtra}
        </div>
        {description && <p className="text-xs text-muted-foreground mt-0.5">{description}</p>}
      </div>
      {action}
    </div>
  )
}

/** Table header cell. */
export const TH = 'text-xs font-medium text-muted-foreground py-3'
