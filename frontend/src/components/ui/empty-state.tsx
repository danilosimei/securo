import type { LucideIcon } from 'lucide-react'

import { cn } from '@/lib/utils'

/**
 * What a list shows when it has nothing: an icon, one short sentence and, when
 * the person can add something, the button of the page's main action.
 * @example `<EmptyState icon={Target} title={t('goals.empty')} action={<Button>...</Button>} />`
 */
export function EmptyState({
  icon: Icon,
  title,
  hint,
  action,
  className,
  testId,
}: {
  icon: LucideIcon
  title: string
  /** An optional second line, smaller. */
  hint?: string
  action?: React.ReactNode
  className?: string
  testId?: string
}) {
  return (
    <div
      className={cn('flex flex-col items-center px-4 py-12 text-center sm:px-5', className)}
      data-testid={testId}
    >
      <Icon size={32} className="mb-3 text-muted-foreground/50" aria-hidden="true" />
      <p className="max-w-sm text-sm text-muted-foreground">{title}</p>
      {hint && <p className="mt-1 max-w-sm text-xs text-muted-foreground">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
