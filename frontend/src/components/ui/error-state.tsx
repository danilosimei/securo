import { AlertCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { extractApiError } from '@/lib/api-errors'
import { cn } from '@/lib/utils'

/**
 * The strip a list shows when its data did not load, with "Try again". Same
 * look as the one on Reports, so a failure never passes for an empty list.
 * @example `{query.isError && <ErrorState error={query.error} onRetry={() => query.refetch()} />}`
 */
export function ErrorState({
  error,
  message,
  onRetry,
  className,
}: {
  /** The failed request's error; its message is shown when it has one. */
  error?: unknown
  /** Used when there is no error message to show. */
  message?: string
  onRetry: () => void
  className?: string
}) {
  const { t } = useTranslation()
  const text = extractApiError(error, message ?? t('common.loadError'))
  return (
    <div
      role="alert"
      className={cn(
        'mb-4 flex items-center justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-2.5 dark:border-rose-500/30 dark:bg-rose-500/10',
        className,
      )}
    >
      <div className="flex min-w-0 items-center gap-2.5">
        <AlertCircle size={16} className="shrink-0 text-rose-600 dark:text-rose-400" />
        <span className="truncate text-sm text-rose-900 dark:text-rose-200">{text}</span>
      </div>
      <button
        type="button"
        onClick={onRetry}
        className="shrink-0 text-sm font-semibold text-rose-600 hover:underline dark:text-rose-400"
      >
        {t('common.retry')}
      </button>
    </div>
  )
}
