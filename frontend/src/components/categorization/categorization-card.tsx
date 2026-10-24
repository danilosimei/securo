import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { AlertCircle, CheckCircle2 } from 'lucide-react'
import { Skeleton } from '@/components/ui/skeleton'
import { useCategorizationSummary } from '@/hooks/use-categorization-summary'
import { useWorkspace } from '@/contexts/workspace-context'

interface Props {
  /** Dashboard summary still loading: show the skeleton, not a wrong state. */
  loading: boolean
  /** Uncategorized transactions from the dashboard summary (the legacy source). */
  uncategorizedCount: number
  /** Already masked and formatted, or null. */
  uncategorizedAmountLabel?: string | null
  onOpenUncategorized: () => void
}

const AMBER_BOX =
  'flex items-center justify-between gap-3 bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/30 rounded-lg px-4 py-2.5 mb-5'
const GREEN_BOX =
  'flex items-center justify-between gap-3 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/30 rounded-lg px-4 py-2.5 mb-5'

/**
 * The dashboard's categorization card: closes the loop between what the
 * automation did and what still needs a person. Takes the old banner's slot
 * and size; with the AI off it keeps the old uncategorized behavior.
 */
export function CategorizationCard({
  loading,
  uncategorizedCount,
  uncategorizedAmountLabel,
  onOpenUncategorized,
}: Props) {
  const { t } = useTranslation()
  const { canManage } = useWorkspace()
  const { data: summary, isLoading } = useCategorizationSummary()

  if (loading || isLoading) {
    return <Skeleton className="h-[42px] w-full rounded-lg mb-5" data-testid="categorization-card-skeleton" />
  }

  // AI off (or the summary unavailable): the old banner, plus a way to turn it on.
  if (!summary || !summary.ai_available) {
    if (uncategorizedCount > 0) {
      return (
        <div className={AMBER_BOX} data-testid="categorization-card-legacy">
          <button
            type="button"
            className="flex flex-1 items-center gap-2.5 min-w-0 text-left cursor-pointer"
            onClick={onOpenUncategorized}
          >
            <AlertCircle size={16} className="shrink-0 text-amber-500" />
            <span className="text-sm text-amber-900 dark:text-amber-200 truncate">
              {t('dashboard.uncategorizedCta', { count: uncategorizedCount })}
              {uncategorizedAmountLabel && (
                <span className="text-amber-700/70 dark:text-amber-300/70"> · {uncategorizedAmountLabel}</span>
              )}
            </span>
          </button>
          <div className="shrink-0 flex items-center gap-3">
            {summary && canManage && (
              <Link
                to="/workspace/settings#categorization"
                className="text-xs text-amber-700 dark:text-amber-300 hover:underline"
              >
                {t('categorization.card.enableAi')}
              </Link>
            )}
            <button
              type="button"
              className="text-sm font-semibold text-amber-600 dark:text-amber-400 hover:underline cursor-pointer"
              onClick={onOpenUncategorized}
            >
              {t('dashboard.categorizeNow')} &rarr;
            </button>
          </div>
        </div>
      )
    }
    return (
      <div className={GREEN_BOX}>
        <div className="flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
          <span className="text-sm text-emerald-900 dark:text-emerald-200">{t('dashboard.allCategorized')}</span>
        </div>
      </div>
    )
  }

  const auto = summary.applied_last_7d
  if (summary.pending_transactions > 0) {
    return (
      <Link
        to="/categorization?tab=review"
        className={`${AMBER_BOX} hover:bg-amber-100 dark:hover:bg-amber-500/20 transition-colors`}
        data-testid="categorization-card-pending"
      >
        <div className="flex items-center gap-2.5 min-w-0">
          <AlertCircle size={16} className="shrink-0 text-amber-500" />
          <span className="text-sm text-amber-900 dark:text-amber-200 truncate">
            {t('categorization.card.toReview', { count: summary.pending_transactions })}
            {auto > 0 && (
              <span className="text-amber-700/70 dark:text-amber-300/70">
                {' · '}
                {t('categorization.card.autoWeek', { count: auto })}
              </span>
            )}
          </span>
        </div>
        <span className="shrink-0 text-sm font-semibold text-amber-600 dark:text-amber-400">
          {t('categorization.card.review')} &rarr;
        </span>
      </Link>
    )
  }

  return (
    <div className={GREEN_BOX} data-testid="categorization-card-clear">
      <div className="flex items-center gap-2.5 min-w-0">
        <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
        <span className="text-sm text-emerald-900 dark:text-emerald-200 truncate">
          {t('categorization.card.allDone')}
          {auto > 0 && (
            <span className="text-emerald-700/70 dark:text-emerald-300/70">
              {' · '}
              {t('categorization.card.autoWeekShort', { count: auto })}
            </span>
          )}
        </span>
      </div>
      {auto > 0 && (
        <Link
          to="/categorization?tab=applied"
          className="shrink-0 text-xs text-emerald-700 dark:text-emerald-300 hover:underline"
        >
          {t('categorization.card.viewApplied')}
        </Link>
      )}
    </div>
  )
}
