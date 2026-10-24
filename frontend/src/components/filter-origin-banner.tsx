import { Filter, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { FilterOrigin } from '@/lib/filter-origin'

/**
 * "Filter from Reports: Oct 1 to Oct 31, Food" with an X to clear. Shown on
 * Transactions when the page was opened with filters from another screen, so
 * a short list never looks like the whole account.
 * @example `<FilterOriginBanner origin="reports" summary="Food" onClear={clearAll} />`
 */
export function FilterOriginBanner({
  origin,
  summary,
  onClear,
}: {
  origin: FilterOrigin
  summary: string
  onClear: () => void
}) {
  const { t } = useTranslation()
  return (
    <div
      data-testid="filter-origin-banner"
      className="mb-4 flex items-center gap-2 rounded-lg border border-primary/15 bg-primary/5 px-3 py-2 text-xs text-primary"
    >
      <Filter size={13} aria-hidden="true" className="shrink-0" />
      <p className="min-w-0 flex-1 truncate" title={summary}>
        {t('transactions.filterFromScreen', { origin: t(`transactions.filterOrigin.${origin}`), summary })}
      </p>
      <button
        type="button"
        onClick={onClear}
        aria-label={t('transactions.filterFromScreenClear')}
        title={t('transactions.filterFromScreenClear')}
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-primary/70 hover:bg-primary/10 hover:text-primary"
      >
        <X size={14} />
      </button>
    </div>
  )
}
