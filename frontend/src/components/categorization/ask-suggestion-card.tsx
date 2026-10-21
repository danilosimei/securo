/** The category an answer of the AI proposes, ready to take: the path (an
 *  existing category, or a new one, maybe in a new group), how sure it is
 *  and how many rows it files, with a big Accept and "Another category". */
import { useTranslation } from 'react-i18next'
import { Check, FolderPlus, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { CategoryIcon } from '@/components/category-icon'
import { CategorySelect } from '@/components/category-select'
import type { AskSuggestion, Category, CategoryGroup, NewCategoryDraft } from '@/types'

/** What an Accept files: an existing category, or one to create. */
export type AskPick = { category_id: string } | { new_category: NewCategoryDraft }

interface AskSuggestionCardProps {
  suggestion: AskSuggestion
  categories: Category[]
  groups: CategoryGroup[]
  /** An Accept is on its way: every answer waits for it. */
  busy: boolean
  /** The group "Another category" offers first when nothing better is known. */
  suggestedGroupId: string | null
  onPick: (pick: AskPick) => void
}

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span className="shrink-0 rounded-full bg-violet-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-700 dark:bg-violet-950 dark:text-violet-300">
      {children}
    </span>
  )
}

export function AskSuggestionCard({
  suggestion,
  categories,
  groups,
  busy,
  suggestedGroupId,
  onPick,
}: AskSuggestionCardProps) {
  const { t } = useTranslation()
  const existing = suggestion.category_id
    ? categories.find((c) => c.id === suggestion.category_id)
    : undefined
  const existingGroup = existing?.group_id ? groups.find((g) => g.id === existing.group_id) : undefined
  const draft = suggestion.category_id ? null : suggestion.new_category
  const pick: AskPick | null = suggestion.category_id
    ? { category_id: suggestion.category_id }
    : draft
      ? { new_category: draft }
      : null

  let path: string
  if (existing) path = existingGroup ? `${existingGroup.name} › ${existing.name}` : existing.name
  else if (draft) path = `${draft.group} › ${draft.name}`
  else path = '—'

  return (
    <div
      data-testid="ask-suggestion"
      className="mt-2 rounded-xl border border-primary/30 bg-card p-3 shadow-sm"
    >
      <div className="flex min-w-0 items-center gap-2">
        {existing ? (
          <CategoryIcon icon={existing.icon} color={existing.color} size="sm" />
        ) : (
          <FolderPlus size={16} aria-hidden="true" className="shrink-0 text-violet-600 dark:text-violet-400" />
        )}
        <span className="min-w-0 truncate text-sm font-semibold text-foreground" title={path}>
          {path}
        </span>
        {draft && <Badge>{t('categorization.ask.newCategory')}</Badge>}
        {draft && !suggestion.group_exists && <Badge>{t('categorization.ask.newGroup')}</Badge>}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        {t('categorization.ask.confidence', { value: suggestion.confidence })}
        {' · '}
        {t('categorization.ask.appliesTo', { count: suggestion.applies_to })}
      </p>
      <div className="mt-3 grid grid-cols-1 gap-2 sm:flex sm:items-center">
        <Button
          type="button"
          className="h-10 gap-1.5 px-5"
          disabled={busy || !pick}
          onClick={() => pick && onPick(pick)}
        >
          {busy ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
          {t('categorization.ask.accept')}
        </Button>
        <CategorySelect
          value=""
          onChange={(id) => id && onPick({ category_id: id })}
          categories={categories}
          groups={groups}
          placeholder={t('categorization.ask.other')}
          disabled={busy}
          onCreateInGroup={(d) => onPick({ new_category: { group: d.group, name: d.name } })}
          suggestedGroupId={existing?.group_id ?? suggestedGroupId}
          className="h-10 w-full px-3 text-sm sm:w-52"
          contentProps={{ className: 'w-72 p-0 overflow-hidden', align: 'start' }}
        />
      </div>
    </div>
  )
}
