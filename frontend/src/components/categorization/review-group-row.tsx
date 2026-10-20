/** One store in the review queue: who it is, how much, why the AI hesitated,
 *  and the one-click answers. Expanding it lists the store's transactions.
 *
 *  The row itself is the keyboard target (roving tabindex, driven by
 *  `ReviewTab`); the buttons inside stay ordinary buttons so a mouse, a
 *  touch screen and a screen reader all reach the same answers. Only the
 *  current row's buttons are Tab stops: the shortcuts and the arrows serve
 *  that row, so Tab need not walk every button of every store. */
import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronRight, FolderPlus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { CategoryIcon } from '@/components/category-icon'
import { CategorySelect } from '@/components/category-select'
import { formatCurrency } from '@/lib/format'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { cn } from '@/lib/utils'
import { storeName } from '@/lib/categorization-review'
import type { Category, CategoryGroup, ReviewGroup, ReviewTransaction } from '@/types'

export interface CategoryInfo {
  category: Category
  groupName: string | null
}

function categoryPath(info: CategoryInfo) {
  return info.groupName ? `${info.groupName} › ${info.category.name}` : info.category.name
}

/** A key cap, shared by the row buttons and the shortcuts legend. */
export function Kbd({ className, ...props }: React.ComponentProps<'kbd'>) {
  return (
    <kbd
      className={cn(
        'inline-flex h-4 min-w-4 items-center justify-center rounded border border-border bg-muted px-1 font-mono text-[10px] font-medium leading-none text-muted-foreground',
        className,
      )}
      {...props}
    />
  )
}

function SuggestionButton({
  info,
  shortcut,
  tabIndex,
  onClick,
}: {
  info: CategoryInfo
  shortcut: string
  tabIndex: number
  onClick: () => void
}) {
  return (
    <Button
      variant="outline"
      size="sm"
      className="col-span-2 h-8 w-full justify-start gap-1.5 px-2.5 sm:w-auto sm:max-w-[15rem]"
      tabIndex={tabIndex}
      title={categoryPath(info)}
      aria-label={categoryPath(info)}
      aria-keyshortcuts={shortcut}
      onClick={onClick}
    >
      <CategoryIcon icon={info.category.icon} color={info.category.color} size="xs" />
      <span className="min-w-0 truncate">
        {info.groupName && <span className="text-muted-foreground">{info.groupName} › </span>}
        {info.category.name}
      </span>
      <Kbd aria-hidden="true" className="ml-auto hidden sm:inline-flex">
        {shortcut}
      </Kbd>
    </Button>
  )
}

interface ReviewGroupRowProps {
  group: ReviewGroup
  index: number
  catalog: Map<string, CategoryInfo>
  categories: Category[]
  categoryGroups: CategoryGroup[]
  canWrite: boolean
  open: boolean
  /** Holds the roving tabindex: the row Tab lands on. */
  current: boolean
  /** Being applied by "accept all": dimmed and inert until it settles. */
  busy: boolean
  /** The shortcuts legend, read out as the row's description. */
  describedBy?: string
  currency: string
  /** Offer the row's own new-category proposal (none bigger covers it). */
  offerNewCategory: boolean
  rowRef: (el: HTMLLIElement | null) => void
  onToggle: () => void
  onFocusRow: () => void
  onAccept: (categoryId: string) => void
  onDismiss: () => void
  onCreateCategory: () => void
}

export function ReviewGroupRow({
  group,
  index,
  catalog,
  categories,
  categoryGroups,
  canWrite,
  open,
  current,
  busy,
  describedBy,
  currency,
  offerNewCategory,
  rowRef,
  onToggle,
  onFocusRow,
  onAccept,
  onDismiss,
  onCreateCategory,
}: ReviewGroupRowProps) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const listId = useId()

  const name = storeName(group)
  // The buttons inside are Tab stops only on the current row.
  const inner = current ? 0 : -1
  const guess = group.suggestion?.category_id ? catalog.get(group.suggestion.category_id) : undefined
  const altId = group.suggestion?.alt_category_id
  const alt = altId && altId !== group.suggestion?.category_id ? catalog.get(altId) : undefined
  const newCategoryPath = group.new_category
    ? `${group.new_category.group} › ${group.new_category.name}`
    : null

  let note: string | null
  if (group.is_person) {
    note = name ? t('categorization.review.whoIs', { name }) : t('categorization.review.whoIsUnknown')
  } else if (group.suggestion?.reason) {
    note = group.suggestion.reason
  } else {
    const sample = group.samples.find((s) => s && s !== name)
    note = sample ? t('categorization.review.example', { text: sample }) : null
  }

  return (
    <li
      ref={rowRef}
      data-review-row=""
      data-index={index}
      tabIndex={current ? 0 : -1}
      aria-describedby={describedBy}
      aria-busy={busy || undefined}
      aria-keyshortcuts={canWrite ? '1 2 x Enter ArrowUp ArrowDown' : 'Enter ArrowUp ArrowDown'}
      onFocus={(e) => {
        if (e.target === e.currentTarget) onFocusRow()
      }}
      onClick={onToggle}
      className={cn(
        'cursor-pointer px-4 py-3 outline-none transition-[background-color,opacity] hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:shadow-[inset_3px_0_0_var(--color-primary)] sm:px-5',
        busy && 'pointer-events-none opacity-50',
      )}
    >
      <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 text-sm font-medium text-foreground">
            <button
              type="button"
              tabIndex={inner}
              aria-expanded={open}
              aria-controls={listId}
              onClick={(e) => {
                // The row toggles on click too; this one must not toggle twice.
                e.stopPropagation()
                onToggle()
              }}
              className="flex min-w-0 cursor-pointer items-center gap-1.5 rounded-sm text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
            >
              <ChevronRight
                size={14}
                aria-hidden="true"
                className={cn('shrink-0 text-muted-foreground transition-transform', open && 'rotate-90')}
              />
              <span className="truncate">{name ?? t('categorization.review.whoIsUnknown')}</span>
            </button>
            {group.is_person && (
              <span className="shrink-0 rounded-full bg-teal-50 px-1.5 py-0.5 text-[10px] font-semibold text-teal-700 dark:bg-teal-950 dark:text-teal-300">
                {t('categorization.review.pix')}
              </span>
            )}
          </div>
          <p className="mt-0.5 pl-5 text-xs text-muted-foreground">
            {t('categorization.review.txCount', { count: group.count })}
            {' · '}
            <span className="font-medium tabular-nums text-foreground">
              {mask(formatCurrency(Number(group.total), currency, locale))}
            </span>
          </p>
          {note && (
            <p
              className={cn(
                'mt-0.5 truncate pl-5 text-xs',
                group.is_person ? 'font-medium text-amber-700 dark:text-amber-300' : 'text-muted-foreground',
              )}
              title={note}
            >
              {note}
            </p>
          )}
        </div>

        {canWrite && (
          <div
            // Phone: suggestions full width, then "Other…" and "Ignore" side by side.
            className="grid grid-cols-2 gap-1.5 pl-5 sm:flex sm:shrink-0 sm:items-center sm:pl-0"
            onClick={(e) => e.stopPropagation()}
          >
            {guess && (
              <SuggestionButton
                info={guess}
                shortcut="1"
                tabIndex={inner}
                onClick={() => onAccept(guess.category.id)}
              />
            )}
            {alt && (
              <SuggestionButton
                info={alt}
                shortcut="2"
                tabIndex={inner}
                onClick={() => onAccept(alt.category.id)}
              />
            )}
            {offerNewCategory && newCategoryPath && (
              <Button
                variant="outline"
                size="sm"
                className="col-span-2 h-8 w-full justify-start gap-1.5 px-2.5 sm:w-auto sm:max-w-[15rem]"
                tabIndex={inner}
                title={newCategoryPath}
                onClick={onCreateCategory}
              >
                <FolderPlus size={14} className="text-violet-600 dark:text-violet-400" />
                <span className="min-w-0 truncate">
                  {t('categorization.review.newCategory', { path: newCategoryPath })}
                </span>
              </Button>
            )}
            <CategorySelect
              value=""
              onChange={(id) => id && onAccept(id)}
              categories={categories}
              groups={categoryGroups}
              placeholder={t('categorization.review.other')}
              creatable
              tabIndex={inner}
              className="h-8 w-full px-2.5 text-xs sm:w-32"
              contentProps={{ className: 'w-72 p-0 overflow-hidden', align: 'end' }}
            />
            <Button
              variant="ghost"
              size="sm"
              className="h-8 w-full gap-1.5 text-muted-foreground sm:w-8 sm:px-0"
              tabIndex={inner}
              aria-label={t('categorization.review.ignore')}
              aria-keyshortcuts="x"
              title={t('categorization.review.ignoreHint')}
              onClick={onDismiss}
            >
              <X size={14} />
              <span className="sm:hidden">{t('categorization.review.ignore')}</span>
            </Button>
          </div>
        )}
      </div>

      {open && (
        <GroupTransactions
          id={listId}
          transactions={group.transactions}
          more={group.transaction_ids.length - group.transactions.length}
        />
      )}
    </li>
  )
}

/** The store's first transactions as the bank sent them, carried by the
 *  queue itself (no request per row); the others are only counted. */
function GroupTransactions({
  id,
  transactions,
  more,
}: {
  id: string
  transactions: ReviewTransaction[]
  more: number
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()

  const showDate = (iso: string) =>
    new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString(dateLocale)
  const amount = (tx: ReviewTransaction) =>
    `${tx.type === 'credit' ? '+' : '−'}${formatCurrency(Math.abs(Number(tx.amount)), tx.currency, locale)}`

  return (
    <div id={id} className="mt-2.5 cursor-default sm:ml-5" onClick={(e) => e.stopPropagation()}>
      <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-muted/30 text-xs">
        {transactions.map((tx) => (
          <li key={tx.id} className="flex items-center gap-3 px-3 py-2">
            <span className="w-20 shrink-0 tabular-nums text-muted-foreground">{showDate(tx.date)}</span>
            <span className="min-w-0 flex-1 truncate text-foreground" title={tx.original_description ?? tx.description}>
              {tx.original_description ?? tx.description}
            </span>
            <span
              className={cn(
                'shrink-0 font-medium tabular-nums',
                tx.type === 'credit' ? 'text-emerald-600 dark:text-emerald-400' : 'text-foreground',
              )}
            >
              {mask(amount(tx))}
            </span>
          </li>
        ))}
      </ul>
      {more > 0 && (
        <p className="mt-1.5 text-xs text-muted-foreground">
          {t('categorization.review.more', { count: more })}
        </p>
      )}
    </div>
  )
}
