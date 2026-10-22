import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useCategorizationPending } from '@/hooks/use-categorization-summary'
import { cn } from '@/lib/utils'
import type { NavItem } from '@/lib/nav-items'

type LinkItem = Extract<NavItem, { type: 'link' }>

/**
 * Pending-work pill for the categorization link: the same amber nudge the
 * rules queue wears, gone entirely at zero. Expanded sidebar shows the
 * number; the collapsed one a dot, since a number does not fit in 40px.
 * Decorative: the count is announced through the link's own name.
 */
function CategorizationBadge({ count, collapsed }: { count: number; collapsed: boolean }) {
  if (count <= 0) return null
  return (
    <>
      <span
        data-testid="nav-badge"
        aria-hidden="true"
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

/** One sidebar destination. Items with a `badgeQueryKey` (today only categorization) show a pending count. */
export function SidebarNavLink({
  item,
  isActive,
  collapsed,
  onNavigate,
}: {
  item: LinkItem
  isActive: boolean
  collapsed: boolean
  onNavigate: () => void
}) {
  const { t } = useTranslation()
  const pending = useCategorizationPending(!!item.badgeQueryKey)
  const count = item.badgeQueryKey ? pending : 0
  const name = t(`nav.${item.key}`)
  // aria-label replaces the content for assistive tech, so the count has to
  // be part of it or it is never announced (expanded or collapsed).
  const accessibleName = count > 0 ? t('categorization.navWithPending', { name, count }) : name
  const Icon = item.icon

  return (
    <Link
      to={item.path}
      data-tour={`nav-${item.key}`}
      onClick={onNavigate}
      title={name}
      aria-label={accessibleName}
      className={cn(
        'relative flex h-8 items-center gap-3 text-[13px] font-medium transition-all rounded-lg px-3',
        isActive
          ? 'bg-primary/[0.08] text-primary border-l-[3px] border-primary pl-[9px]'
          : 'text-sidebar-muted hover:bg-sidebar-accent hover:text-sidebar-foreground',
        collapsed && 'lg:h-10 lg:w-10 lg:justify-center lg:border-l-0 lg:px-0 lg:pl-0',
      )}
    >
      <Icon
        size={17}
        className={cn(
          'shrink-0',
          isActive ? 'text-primary' : 'text-sidebar-muted',
          collapsed && 'lg:h-5 lg:w-5',
        )}
      />
      <span className={cn(collapsed && 'lg:hidden')}>{name}</span>
      {item.badgeQueryKey && <CategorizationBadge count={count} collapsed={collapsed} />}
    </Link>
  )
}
