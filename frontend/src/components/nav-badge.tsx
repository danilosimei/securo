import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { categorization } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * Pending-work pill for a sidebar link: the same amber nudge the rules queue
 * wears, gone entirely when there is nothing waiting. It shares its query key
 * with the categorization page, so a run finishing or a review accepted
 * updates the menu without a second request.
 *
 * Expanded sidebar: the pill. Collapsed (desktop): a small dot on the icon,
 * since a number does not fit in a 40px square.
 */
export function NavBadge({
  queryKey,
  collapsed = false,
}: {
  queryKey: readonly string[]
  /** Desktop sidebar collapsed to icons only. */
  collapsed?: boolean
}) {
  const { t } = useTranslation()
  const { data } = useQuery({
    queryKey: [...queryKey],
    queryFn: categorization.summary,
    staleTime: 60_000,
  })
  const count = data?.pending_groups ?? 0
  if (count <= 0) return null
  const label = t('categorization.pendingBadge', { count })
  return (
    <>
      <span
        data-testid="nav-badge"
        title={label}
        aria-label={label}
        className={cn(
          'ml-auto inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full bg-amber-500 text-white text-[10px] font-semibold tabular-nums',
          collapsed && 'lg:hidden',
        )}
      >
        {count}
      </span>
      <span
        data-testid="nav-badge-dot"
        aria-hidden="true"
        className={cn(
          'hidden absolute top-1.5 right-1.5 h-2 w-2 rounded-full bg-amber-500',
          collapsed && 'lg:block',
        )}
      />
    </>
  )
}
