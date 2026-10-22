import { Search, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/utils'

/** Look of the search card; the transactions bar wraps its own parts in it. */
export const SEARCH_BAR_CLASS = cn(
  'group/filterbar rounded-xl border border-border bg-card shadow-sm transition-colors',
  'focus-within:border-primary/40 focus-within:ring-[3px] focus-within:ring-primary/10',
)

/**
 * The search box every list uses: a card with a magnifier, the text and an X to
 * clear it. Extra filters for the list go in `children`, at the right.
 * @example `<SearchBar value={q} onChange={setQ} placeholder={t('payees.searchPlaceholder')} />`
 */
export function SearchBar({
  value,
  onChange,
  placeholder,
  children,
  footer,
  compact = false,
  className,
  testId,
  inputTestId,
}: {
  value: string
  onChange: (value: string) => void
  placeholder: string
  /** Filters that sit with the search, at the right. */
  children?: React.ReactNode
  /** A row under the search, inside the card (chips of the active filters). */
  footer?: React.ReactNode
  /** Shorter box, for a search inside a table toolbar. */
  compact?: boolean
  className?: string
  testId?: string
  inputTestId?: string
}) {
  const { t } = useTranslation()
  return (
    <div className={cn(SEARCH_BAR_CLASS, className)} data-testid={testId}>
      <div className={cn('flex items-center gap-1.5 px-2', compact ? 'py-0.5' : 'py-1.5')}>
        <div className={cn('flex min-w-0 flex-1 items-center gap-1 px-2.5', compact ? 'min-h-8 py-0.5' : 'min-h-9 py-1')}>
          <Search size={15} className="pointer-events-none shrink-0 text-muted-foreground/70" />
          <input
            type="search"
            aria-label={placeholder}
            placeholder={placeholder}
            value={value}
            data-testid={inputTestId}
            onChange={(event) => onChange(event.target.value)}
            className="min-w-0 flex-1 appearance-none bg-transparent px-1.5 text-[13.5px] outline-none placeholder:text-muted-foreground/75 [&::-webkit-search-cancel-button]:hidden [&::-webkit-search-cancel-button]:appearance-none"
          />
          {value && (
            <button
              type="button"
              aria-label={t('common.clearSearch')}
              title={t('common.clearSearch')}
              onClick={() => onChange('')}
              className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <X size={15} />
            </button>
          )}
        </div>
        {children && <div className="ml-auto flex shrink-0 items-center gap-1 pl-1">{children}</div>}
      </div>
      {footer && <div className="border-t border-border/60 px-2 py-1.5">{footer}</div>}
    </div>
  )
}
