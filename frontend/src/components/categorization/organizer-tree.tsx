/** The tree an organizer proposal leaves behind, next to its operations:
 *  new items green, renamed and moved ones with what they were, merged ones
 *  struck through. Hovering an operation lights up what it touches. */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { predictTree, type PredictedCategory, type PredictedGroup } from '@/lib/categorization-organizer'
import { cn } from '@/lib/utils'
import { SectionCard, SectionHeader } from '@/components/invoice-ui'
import { CategoryIcon } from '@/components/category-icon'
import type { OrganizerOp } from '@/types'

export function PredictedTree({
  className,
  groups,
  categories,
  ops,
  highlight,
}: {
  className?: string
  groups: Parameters<typeof predictTree>[0]
  categories: Parameters<typeof predictTree>[1]
  ops: OrganizerOp[]
  highlight: string | null
}) {
  const { t } = useTranslation()
  const tree = useMemo(() => predictTree(groups, categories, ops), [groups, categories, ops])
  // Groups the proposal touches start open, the rest closed. Keyed by name:
  // a group stays open once its op is applied (a proposed group gets an id
  // then, but keeps its name).
  const touched = tree.filter((g) => g.touched).map((g) => treeKey(g))
  const [seen, setSeen] = useState<Set<string>>(() => new Set(touched))
  if (touched.some((key) => !seen.has(key))) setSeen(new Set([...seen, ...touched]))
  const [flipped, setFlipped] = useState<Set<string>>(() => new Set())
  const toggle = (key: string) =>
    setFlipped((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  return (
    <SectionCard className={className}>
      <SectionHeader title={t('categorization.organize.treeTitle')} description={t('categorization.organize.treeHint')} />
      <div data-testid="organizer-tree">
        {tree.map((group) => (
          <TreeGroup
            key={group.key || 'ungrouped'}
            group={group}
            open={(group.touched || seen.has(treeKey(group))) !== flipped.has(treeKey(group))}
            onToggle={() => toggle(treeKey(group))}
            highlight={highlight}
          />
        ))}
      </div>
    </SectionCard>
  )
}

const treeKey = (group: PredictedGroup) => (group.name ?? '').trim().toLocaleLowerCase()

const NEW_BADGE =
  'shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200'

function TreeGroup({
  group,
  open,
  onToggle,
  highlight,
}: {
  group: PredictedGroup
  open: boolean
  onToggle: () => void
  highlight: string | null
}) {
  const { t } = useTranslation()
  const lit = highlight !== null && group.opIds.includes(highlight)
  return (
    <div>
      <button
        type="button"
        className={cn(
          'flex w-full items-center gap-2 border-b border-border bg-muted/40 px-4 py-2.5 text-left sm:px-5',
          lit && 'bg-amber-100/60 dark:bg-amber-900/20',
        )}
        onClick={onToggle}
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown size={14} className="shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight size={14} className="shrink-0 text-muted-foreground" />
        )}
        {group.name !== null && <CategoryIcon icon={group.icon ?? 'folder'} color={group.color ?? '#10b981'} size="sm" />}
        <span
          className={cn(
            'truncate text-sm font-semibold',
            group.name === null && 'text-muted-foreground',
            group.isNew && 'text-emerald-700 dark:text-emerald-300',
          )}
          style={group.name !== null && !group.isNew && group.color ? { color: group.color } : undefined}
        >
          {group.name ?? t('categorization.organize.noGroup')}
        </span>
        {group.isNew && <span className={NEW_BADGE}>{t('categorization.organize.newBadge')}</span>}
        {group.renamedFrom && (
          <span className="truncate text-xs text-muted-foreground">
            {t('categorization.organize.before', { name: group.renamedFrom })}
          </span>
        )}
        <span className="ml-auto shrink-0 text-xs text-muted-foreground">
          ({group.categories.filter((c) => c.mergedInto === undefined).length})
        </span>
      </button>
      {open && group.categories.map((category) => (
        <TreeCategory key={category.key} category={category} highlight={highlight} />
      ))}
    </div>
  )
}

function TreeCategory({ category, highlight }: { category: PredictedCategory; highlight: string | null }) {
  const { t } = useTranslation()
  const merged = category.mergedInto !== undefined
  const lit = highlight !== null && category.opIds.includes(highlight)
  const hints: string[] = []
  if (category.renamedFrom) hints.push(t('categorization.organize.before', { name: category.renamedFrom }))
  if (category.movedFrom !== undefined) {
    hints.push(
      category.movedFrom === null
        ? t('categorization.organize.movedFromNoGroup')
        : t('categorization.organize.movedFrom', { group: category.movedFrom }),
    )
  }
  if (merged) hints.push(t('categorization.organize.mergedInto', { name: category.mergedInto }))

  return (
    <div
      data-tree-category
      className={cn(
        'flex items-center gap-2.5 border-b border-border py-2 pl-9 pr-4 last:border-0 transition-colors sm:pl-12 sm:pr-5',
        category.isNew && 'bg-emerald-50/50 dark:bg-emerald-950/15',
        lit && 'bg-amber-100/60 dark:bg-amber-900/20',
      )}
    >
      <CategoryIcon
        icon={category.icon}
        color={category.color}
        size="sm"
        className={merged ? 'opacity-40' : undefined}
      />
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5">
        <span
          className={cn(
            'truncate text-sm',
            category.isNew && 'font-medium text-emerald-700 dark:text-emerald-300',
            merged && 'text-muted-foreground line-through',
            !category.isNew && !merged && 'text-foreground',
          )}
        >
          {category.name}
        </span>
        {category.isNew && <span className={NEW_BADGE}>{t('categorization.organize.newBadge')}</span>}
        {hints.map((hint) => (
          <span key={hint} className="text-xs text-muted-foreground">
            {hint}
          </span>
        ))}
      </div>
    </div>
  )
}
