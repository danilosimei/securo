import {
  ArrowLeftRight,
  BarChart3,
  Building2,
  FolderOpen,
  LayoutDashboard,
  Landmark,
  PiggyBank,
  Receipt,
  Repeat,
  SlidersHorizontal,
  Sparkles,
  Split,
  Tag,
  Target,
  Upload,
  Users,
} from 'lucide-react'
// Relative, not aliased: this file is pulled into the test project,
// which compiles without the `@/*` path mapping (see tsconfig.node.json).
import type { ModuleId } from './modules'

/**
 * Sidebar destinations. Lives outside `app-layout.tsx` so the filtering
 * below can be tested without mounting the whole layout.
 *
 * Every link carries the module it belongs to. That is what makes the
 * "a personal workspace shows exactly what it always showed" guarantee
 * checkable rather than aspirational.
 */
export type NavItem =
  | {
      type: 'link'
      key: string
      path: string
      icon: React.ElementType
      /** Absent for destinations every workspace has (the dashboard). */
      module?: ModuleId
      /** Query whose data carries a pending count, shown as an amber pill. */
      badgeQueryKey?: readonly string[]
    }
  | { type: 'separator'; labelKey: string }

export const navItems: NavItem[] = [
  { type: 'link', key: 'dashboard', path: '/', icon: LayoutDashboard },
  { type: 'separator', labelKey: 'nav.groupDaily' },
  { type: 'link', key: 'transactions', path: '/transactions', icon: ArrowLeftRight, module: 'transactions' },
  { type: 'link', key: 'accounts', path: '/accounts', icon: Building2, module: 'accounts' },
  { type: 'link', key: 'invoices', path: '/invoices', icon: Receipt, module: 'invoices' },
  { type: 'link', key: 'import', path: '/import', icon: Upload, module: 'import' },
  { type: 'separator', labelKey: 'nav.groupPlanning' },
  { type: 'link', key: 'budgets', path: '/budgets', icon: PiggyBank, module: 'budgets' },
  { type: 'link', key: 'goals', path: '/goals', icon: Target, module: 'goals' },
  { type: 'link', key: 'recurring', path: '/recurring', icon: Repeat, module: 'recurring' },
  { type: 'separator', labelKey: 'nav.groupAnalysis' },
  { type: 'link', key: 'reports', path: '/reports', icon: BarChart3, module: 'reports' },
  { type: 'link', key: 'assets', path: '/assets', icon: Landmark, module: 'assets' },
  { type: 'separator', labelKey: 'nav.groupOrganization' },
  { type: 'link', key: 'categories', path: '/categories', icon: Tag, module: 'categories' },
  { type: 'link', key: 'categorization', path: '/categorization', icon: Sparkles, module: 'categories', badgeQueryKey: ['categorization', 'summary'] },
  { type: 'link', key: 'rules', path: '/rules', icon: SlidersHorizontal, module: 'rules' },
  { type: 'link', key: 'payees', path: '/payees', icon: Users, module: 'payees' },
  { type: 'link', key: 'splitGroups', path: '/groups', icon: Split, module: 'split_groups' },
  { type: 'link', key: 'collections', path: '/collections', icon: FolderOpen, module: 'accounts' },
]

/**
 * Drop links whose module is off, then drop any section header left
 * with nothing under it. Without the second pass, hiding a section's
 * only link leaves a floating heading.
 */
export function visibleNavItems(
  items: NavItem[],
  hasModule: (id: ModuleId) => boolean,
): NavItem[] {
  const kept = items.filter((item) => item.type !== 'link' || !item.module || hasModule(item.module))
  return kept.filter((item, index) => {
    if (item.type !== 'separator') return true
    // Links always follow their own header, so the item right after a
    // header is either one of its links or the next header.
    const next = kept[index + 1]
    return next !== undefined && next.type === 'link'
  })
}
