import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useCollectionFilter } from '@/contexts/collection-filter-context'
import { Check, ChevronsUpDown, Layers, Settings2 } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * Global "active collection" selector (issue #105). Filters the app to the
 * accounts (and wallets) of the chosen collection.
 *
 * Lives at the top of the side menu, under the search, so it sits in one fixed
 * place on every page and pushes no content down. With a collection active the
 * trigger carries its color. Hidden entirely until the user has at least one
 * collection, so it never clutters the UI for people who don't use the feature.
 * In the collapsed rail it is a single icon.
 */
export function CollectionSelector({ collapsed = false }: { collapsed?: boolean }) {
  const { t } = useTranslation()
  const nav = useNavigate()
  const { collections, activeCollection, setActiveCollectionId } = useCollectionFilter()

  if (collections.length === 0) return null

  const menu = (
    <DropdownMenuContent align="start" className="w-72">
      <DropdownMenuItem onClick={() => setActiveCollectionId(null)} className="flex items-center gap-2">
        <Layers size={14} className="text-muted-foreground" />
        <span className="flex-1">{t('collections.allAccounts')}</span>
        {!activeCollection && <Check size={14} className="text-primary" />}
      </DropdownMenuItem>
      <DropdownMenuSeparator />
      {collections.map((c) => (
        <DropdownMenuItem key={c.id} onClick={() => setActiveCollectionId(c.id)} className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: c.color }} />
          <span className="min-w-0 flex-1 truncate">{c.name}</span>
          {/* Spelled out and translated: "8a · 3w" read as cropped text, and the
              a/w abbreviations were English-only in a ten-locale UI. */}
          <span className="shrink-0 whitespace-nowrap text-[11px] tabular-nums text-muted-foreground/70">
            {[
              c.account_count > 0 && t('collections.accountCount', { count: c.account_count }),
              c.wallet_count > 0 && t('collections.walletCount', { count: c.wallet_count }),
            ].filter(Boolean).join(' · ')}
          </span>
          {activeCollection?.id === c.id && <Check size={14} className="shrink-0 text-primary" />}
        </DropdownMenuItem>
      ))}
      <DropdownMenuSeparator />
      <DropdownMenuItem onClick={() => nav('/collections')} className="flex items-center gap-2 text-muted-foreground">
        <Settings2 size={14} />
        {t('collections.manage')}
      </DropdownMenuItem>
    </DropdownMenuContent>
  )

  return (
    <div className={cn('px-3 pt-2', collapsed && 'lg:px-2')}>
      {/* Non-modal: the default locks body scroll while open, which hides the
          page scrollbar and shifts the layout. */}
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <button
            data-testid="collection-selector"
            title={activeCollection?.name ?? t('collections.allAccounts')}
            aria-label={`${t('collections.viewing')}: ${activeCollection?.name ?? t('collections.allAccounts')}`}
            className={cn(
              'flex w-full items-center gap-2 rounded-lg border border-sidebar-border/60 bg-sidebar-accent/30 px-2.5 py-1.5 text-left transition-colors hover:bg-sidebar-accent/50',
              collapsed && 'lg:justify-center lg:px-0',
            )}
            style={
              activeCollection
                ? { borderColor: `${activeCollection.color}66`, backgroundColor: `${activeCollection.color}1f` }
                : undefined
            }
          >
            {activeCollection ? (
              <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: activeCollection.color }} />
            ) : (
              <Layers size={13} className="shrink-0 text-sidebar-muted" />
            )}
            <span className={cn('min-w-0 flex-1 truncate text-xs font-medium text-sidebar-foreground', collapsed && 'lg:hidden')}>
              {activeCollection?.name ?? t('collections.allAccounts')}
            </span>
            <ChevronsUpDown size={13} className={cn('shrink-0 text-sidebar-muted', collapsed && 'lg:hidden')} />
          </button>
        </DropdownMenuTrigger>
        {menu}
      </DropdownMenu>
    </div>
  )
}
