import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { HelpCircle } from 'lucide-react'

import { ORIGIN_STYLES } from '@/lib/categorization-origins'
import { cn } from '@/lib/utils'
import type { CategorySource } from '@/types'

interface CategorizationOriginBadgeProps {
  source?: CategorySource
  /** A suggestion for this row waits for review; wins over the origin. */
  pending?: boolean
  /** Why the category was chosen, when the caller has it. */
  reason?: string | null
  /** Icon only, for dense rows (mobile). */
  compact?: boolean
}

/**
 * Discreet seal for categories the system chose. A person's own choices (and
 * everything else) render nothing, so the list stays quiet by default.
 */
export function CategorizationOriginBadge({
  source,
  pending = false,
  reason,
  compact = false,
}: CategorizationOriginBadgeProps) {
  const { t } = useTranslation()

  const size = compact ? 'text-[9px] px-1 py-0.5' : 'text-[10px] px-1.5 py-0.5 gap-1'
  const base = cn(
    'inline-flex items-center shrink-0 font-semibold uppercase tracking-wide rounded-full border',
    size,
  )
  const icon = compact ? 'h-2.5 w-2.5' : 'h-3 w-3'

  if (pending) {
    const label = t('categorization.origin.review')
    return (
      <Link
        to="/categorization?tab=review"
        onClick={(e) => e.stopPropagation()}
        title={reason || t('categorization.origin.reviewTooltip')}
        aria-label={t('categorization.origin.reviewAria')}
        className={cn(
          base,
          'border-amber-200 bg-amber-50 text-amber-700 transition-colors hover:bg-amber-100 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300 dark:hover:bg-amber-950/70',
        )}
      >
        <HelpCircle className={icon} />
        {!compact && label}
      </Link>
    )
  }

  if (source !== 'ai' && source !== 'memory' && source !== 'rule') return null

  const { Icon, tone } = ORIGIN_STYLES[source]
  const label = t(`categorization.origin.${source}`)
  return (
    <span
      title={reason || t(`categorization.origin.${source}Tooltip`)}
      aria-label={compact ? label : undefined}
      className={cn(base, tone)}
    >
      <Icon className={icon} />
      {!compact && label}
    </span>
  )
}
