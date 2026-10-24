import { useState, useMemo, useContext, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  ChevronDownIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CircleAlertIcon,
  FolderInputIcon,
  FolderPlusIcon,
  PlusIcon,
  type LucideIcon,
} from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Command, CommandInput, CommandList, CommandEmpty, CommandGroup, CommandItem } from '@/components/ui/command'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { categories as categoriesApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateCategoryQueries } from '@/lib/invalidate-queries'
import {
  CREATE_NAME_MAX,
  cleanCreateName,
  createNameTooLong,
  groupedCreateOptions,
  inlineCreateItemValue,
  inlineCreateName,
  type GroupedCreateDraft,
  type GroupedCreateOption,
} from '@/lib/inline-create'
import type { Category, CategoryGroup } from '@/types'
import { CategoryIcon } from '@/components/category-icon'
import { cn, normalizeText } from '@/lib/utils'
import {
  isCategoryHiddenFromSelection,
  resolveSelectedCategory,
} from '@/lib/category-selection-utils'

// Same starting look the categories page gives a new category, so one made
// from a picker is indistinguishable from one made there.
const NEW_CATEGORY_ICON = 'circle-help'
const NEW_CATEGORY_COLOR = '#6366f1'
// Tall enough to scan many categories at once, but never past the screen
// edge: the popover's available height minus the search box.
const LIST_HEIGHT = 'max-h-[min(560px,calc(var(--radix-popover-content-available-height,560px)-3.5rem))]'

/** The last row of the list, shown whatever the search. */
export interface CategorySelectFooterAction {
  label: string
  icon?: LucideIcon
  disabled?: boolean
  /** Why it is disabled, or what it does: the row's tooltip. */
  hint?: string
  onSelect: () => void
}

interface CategorySelectProps {
  value: string
  onChange: (value: string) => void
  categories: Category[]
  groups: CategoryGroup[]
  currentCategory?: Category | null
  placeholder?: string
  disabled?: boolean
  className?: string
  allowNone?: boolean
  /**
   * Offer to create a category named after the search text when nothing
   * matches it. Only shown to members who can write to the workspace.
   */
  creatable?: boolean
  /**
   * Offer to create the searched name inside a group, Notion style: in the
   * suggested group, in another group (a second step lists them), or as a
   * new group; `Group / Name` goes straight to that group. The host creates
   * it (nothing is sent from here). Replaces `creatable`'s flat creation and,
   * like it, only reaches members who can write.
   */
  onCreateInGroup?: (draft: GroupedCreateDraft) => void
  /** The group offered first by `onCreateInGroup`. */
  suggestedGroupId?: string | null
  footerAction?: CategorySelectFooterAction
  contentProps?: React.ComponentProps<typeof PopoverContent>
  /** The trigger's tab stop, for a host that manages focus (roving tabindex). */
  tabIndex?: number
  /** Show the chosen category's icon in the trigger instead of its color dot. */
  showIcon?: boolean
}


export function CategorySelect({
  value,
  onChange,
  categories,
  groups,
  currentCategory,
  placeholder,
  disabled = false,
  className,
  allowNone = false,
  creatable = false,
  onCreateInGroup,
  suggestedGroupId = null,
  footerAction,
  contentProps,
  tabIndex,
  showIcon = false,
}: CategorySelectProps) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  // "Create in another group": the name waiting for its group, and the
  // search over the groups. Null is the ordinary list.
  const [pickingFor, setPickingFor] = useState<string | null>(null)
  const [groupSearch, setGroupSearch] = useState('')
  // "New category or group": the search box takes the new name and the list
  // shows only the ways to create it.
  const [naming, setNaming] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  // The category just created here. The list catches up once the refetch
  // lands; until then this keeps the trigger showing the new name.
  const [created, setCreated] = useState<Category | null>(null)
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  // Read the context directly rather than through useWorkspace, so the
  // picker still renders (without creation) outside a workspace provider.
  const canWrite = useContext(WorkspaceContext)?.canWrite ?? false
  const createsInGroup = !!onCreateInGroup && canWrite
  const canCreate = creatable && canWrite && !onCreateInGroup
  const offersCreate = createsInGroup || canCreate

  const createMutation = useMutation({
    mutationFn: (name: string) =>
      categoriesApi.create({ name, icon: NEW_CATEGORY_ICON, color: NEW_CATEGORY_COLOR }),
    onSuccess: (category) => {
      // Only the category lists refetch. Whatever form hosts this picker
      // (an import preview, a half-filled transaction) keeps its state.
      invalidateCategoryQueries(queryClient)
      setCreated(category)
      onChange(category.id)
      handleOpenChange(false)
      toast.success(t('categories.created'))
    },
    onError: (err: unknown) => toast.error(extractApiError(err, t('common.error'))),
  })

  function handleOpenChange(next: boolean) {
    setOpen(next)
    if (!next) {
      setSearch('')
      setPickingFor(null)
      setGroupSearch('')
      setNaming(false)
    }
  }

  function leaveNamingStep() {
    setNaming(false)
    setSearch('')
  }

  function leaveGroupStep() {
    setPickingFor(null)
    setGroupSearch('')
  }

  // Switching steps keeps the typing in the one search box, even after a
  // click on a row or on the back arrow took the focus away.
  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open, pickingFor, naming])

  function createInGroup(draft: GroupedCreateDraft) {
    handleOpenChange(false)
    onCreateInGroup?.(draft)
  }

  function selectCreateOption(option: GroupedCreateOption) {
    if (option.kind === 'pickGroup') {
      setPickingFor(option.name)
      setGroupSearch('')
    } else {
      createInGroup(option.draft)
    }
  }

  function createOptionLabel(option: GroupedCreateOption) {
    if (option.kind === 'pickGroup') return t('categorization.create.pickGroup', { name: option.name })
    if (option.kind === 'newGroup') return t('categorization.create.newGroup', { name: option.draft.name })
    return option.draft.groupExists
      ? t('categorization.create.inGroup', { name: option.draft.name, group: option.draft.group })
      : t('categorization.create.inNewGroup', { name: option.draft.name, group: option.draft.group })
  }

  const createName = canCreate
    ? inlineCreateName(search, (categories ?? []).map((c) => c.name))
    : null
  const createOptions = createsInGroup
    ? groupedCreateOptions(search, categories ?? [], groups ?? [], suggestedGroupId)
    : []
  // Over the server's ceiling: say so rather than offer a create that fails.
  const searchTooLong = createsInGroup && search.trim() !== '' && createNameTooLong(search)
  const tooLongLabel = t('categorization.create.tooLong', { max: CREATE_NAME_MAX })

  // The second step lists the visible groups, the suggested one first (so
  // typing its name still finds it); a hidden group would hide the category.
  const pickableGroups = useMemo(() => {
    const visible = (groups ?? []).filter((g) => !g.is_hidden)
    return [
      ...visible.filter((g) => g.id === suggestedGroupId),
      ...visible.filter((g) => g.id !== suggestedGroupId),
    ]
  }, [groups, suggestedGroupId])
  const groupSearchName = cleanCreateName(groupSearch)
  const groupSearchTooLong = groupSearchName !== '' && [...groupSearchName].length > CREATE_NAME_MAX
  const newGroupFromSearch =
    pickingFor !== null
    && groupSearchName !== ''
    && !groupSearchTooLong
    && !pickableGroups.some((g) => normalizeText(cleanCreateName(g.name)) === normalizeText(groupSearchName))

  const resolvedPlaceholder = placeholder ?? t('transactions.selectCategory', 'Select category')

  const displayGroups = useMemo(() => {
    const ungrouped = (categories ?? []).filter((c) => !c.group_id)
    if (ungrouped.length === 0) return groups

    return [
      ...groups,
      {
        id: 'ungrouped-virtual',
        name: t('groups.noGroup'),
        categories: ungrouped,
      } as CategoryGroup,
    ]
  }, [categories, groups, t])

  const selectedCategory = useMemo(() => {
    const fallback = created && created.id === value ? created : currentCategory
    return resolveSelectedCategory(categories ?? [], value, fallback)
  }, [categories, created, currentCategory, value])
  // A category created a moment ago is not hidden, only not refetched yet.
  const selectedCategoryIsHidden =
    selectedCategory !== created
    && isCategoryHiddenFromSelection(categories ?? [], selectedCategory)

  // The ways to create the searched name: at the end of the list, or the
  // whole list in the naming step.
  const createItems = (
    <>
      {createName && (
        <CommandGroup>
          <CommandItem
            value={inlineCreateItemValue(search)}
            disabled={createMutation.isPending}
            onSelect={() => createMutation.mutate(createName)}
            className="cursor-pointer"
          >
            <PlusIcon className="size-4 shrink-0 text-muted-foreground" />
            <span className="truncate">{t('common.createNamed', { name: createName })}</span>
          </CommandItem>
        </CommandGroup>
      )}
      {createOptions.length > 0 && (
        <CommandGroup>
          {createOptions.map((option) => {
            const label = createOptionLabel(option)
            const Icon =
              option.kind === 'pickGroup'
                ? FolderInputIcon
                : option.kind === 'newGroup' || !option.draft.groupExists
                  ? FolderPlusIcon
                  : PlusIcon
            return (
              <CommandItem
                key={option.kind}
                // Carries the search text so the filter keeps the row.
                value={`${inlineCreateItemValue(search)} ${option.kind}`}
                onSelect={() => selectCreateOption(option)}
                title={label}
                className="cursor-pointer"
              >
                <Icon className="size-4 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate">{label}</span>
                {option.kind === 'pickGroup' && (
                  <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground" />
                )}
              </CommandItem>
            )
          })}
        </CommandGroup>
      )}
      {searchTooLong && (
        <CommandGroup>
          <CommandItem value={inlineCreateItemValue(search)} disabled title={tooLongLabel}>
            <CircleAlertIcon className="size-4 shrink-0 text-muted-foreground" />
            <span className="truncate">{tooLongLabel}</span>
          </CommandItem>
        </CommandGroup>
      )}
    </>
  )

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          tabIndex={tabIndex}
          className={cn(
            "flex w-full items-center justify-between gap-2 rounded-md border border-input bg-card px-3 py-2 text-sm text-left shadow-xs transition-[color,box-shadow] outline-hidden focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 dark:hover:bg-input/50 h-9 cursor-pointer",
            className
          )}
        >
          <span className="flex items-center gap-2 min-w-0 truncate">
            {selectedCategory ? (
              <>
                {showIcon ? (
                  <CategoryIcon icon={selectedCategory.icon} color={selectedCategory.color} size="sm" />
                ) : selectedCategory.color ? (
                  <span
                    className="size-2.5 shrink-0 rounded-full border border-black/5"
                    style={{ backgroundColor: selectedCategory.color }}
                  />
                ) : null}
                <span className="truncate">{selectedCategory.name}</span>
                {selectedCategoryIsHidden && (
                  <span className="shrink-0 rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                    {t('categories.hiddenBadge')}
                  </span>
                )}
              </>
            ) : value === '' && allowNone ? (
              <span className="italic text-muted-foreground truncate">{t('transactions.noCategory')}</span>
            ) : (
              <span className="text-muted-foreground truncate">{resolvedPlaceholder}</span>
            )}
          </span>
          <ChevronDownIcon className="size-4 shrink-0 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-[var(--radix-popover-trigger-width)] p-0 overflow-hidden"
        {...contentProps}
        onEscapeKeyDown={(e) => {
          contentProps?.onEscapeKeyDown?.(e)
          // Escape on a step steps back instead of closing.
          if (pickingFor !== null) {
            e.preventDefault()
            leaveGroupStep()
          } else if (naming) {
            e.preventDefault()
            leaveNamingStep()
          }
        }}
      >
        <Command
          filter={(itemValue, search) => {
            return normalizeText(itemValue).includes(normalizeText(search)) ? 1 : 0
          }}
        >
          {pickingFor !== null && (
            <div className="flex items-center gap-1 border-b border-border px-1.5 py-1 text-xs text-muted-foreground">
              <button
                type="button"
                aria-label={t('common.back')}
                onClick={leaveGroupStep}
                className="inline-flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-sm hover:bg-muted hover:text-foreground"
              >
                <ChevronLeftIcon className="size-4" />
              </button>
              <span className="truncate" title={t('categorization.create.pickGroup', { name: pickingFor })}>
                {t('categorization.create.pickGroup', { name: pickingFor })}
              </span>
            </div>
          )}
          {pickingFor === null && naming && (
            <div className="flex items-center gap-1 border-b border-border px-1.5 py-1 text-xs text-muted-foreground">
              <button
                type="button"
                aria-label={t('common.back')}
                onClick={leaveNamingStep}
                className="inline-flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-sm hover:bg-muted hover:text-foreground"
              >
                <ChevronLeftIcon className="size-4" />
              </button>
              <span className="truncate">{t('categorization.create.naming')}</span>
            </div>
          )}
          <CommandInput
            ref={inputRef}
            placeholder={
              pickingFor !== null
                ? t('categorization.create.chooseGroup')
                : naming
                  ? t(createsInGroup ? 'categorization.create.namePlaceholder' : 'categorization.create.nameOnly')
                  : offersCreate
                    ? t('categorization.create.searchOrCreate')
                    : t('transactions.searchCategory')
            }
            value={pickingFor !== null ? groupSearch : search}
            onValueChange={pickingFor !== null ? setGroupSearch : setSearch}
            onKeyDown={(e) => {
              // Backspace on an empty group search steps back, like Escape.
              if (pickingFor !== null && e.key === 'Backspace' && groupSearch === '') {
                e.preventDefault()
                leaveGroupStep()
              } else if (pickingFor === null && naming && e.key === 'Backspace' && search === '') {
                e.preventDefault()
                leaveNamingStep()
              }
            }}
          />
          {pickingFor !== null ? (
            <CommandList className={LIST_HEIGHT}>
              <CommandEmpty>{t('categorization.create.noGroup')}</CommandEmpty>
              <CommandGroup>
                {pickableGroups.map((group) => (
                  <CommandItem
                    key={group.id}
                    value={group.name}
                    onSelect={() => createInGroup({ group: group.name, name: pickingFor, groupExists: true })}
                    title={group.name}
                    className="cursor-pointer"
                  >
                    <span className="truncate">{group.name}</span>
                  </CommandItem>
                ))}
                {newGroupFromSearch && (
                  <CommandItem
                    value={inlineCreateItemValue(groupSearch)}
                    onSelect={() =>
                      createInGroup({ group: groupSearchName, name: pickingFor, groupExists: false })
                    }
                    title={t('categorization.create.inNewGroup', { name: pickingFor, group: groupSearchName })}
                    className="cursor-pointer"
                  >
                    <FolderPlusIcon className="size-4 shrink-0 text-muted-foreground" />
                    <span className="truncate">
                      {t('categorization.create.inNewGroup', { name: pickingFor, group: groupSearchName })}
                    </span>
                  </CommandItem>
                )}
                {groupSearchTooLong && (
                  <CommandItem value={inlineCreateItemValue(groupSearch)} disabled title={tooLongLabel}>
                    <CircleAlertIcon className="size-4 shrink-0 text-muted-foreground" />
                    <span className="truncate">{tooLongLabel}</span>
                  </CommandItem>
                )}
              </CommandGroup>
            </CommandList>
          ) : naming ? (
            <CommandList className={LIST_HEIGHT}>
              <CommandEmpty>
                {search.trim() !== ''
                  ? t('categorization.create.exists')
                  : t(createsInGroup ? 'categorization.create.typeName' : 'categorization.create.typeNameOnly')}
              </CommandEmpty>
              {createItems}
            </CommandList>
          ) : (
            <CommandList className={LIST_HEIGHT}>
              <CommandEmpty>{t('transactions.noCategoryFound')}</CommandEmpty>
              {offersCreate && search.trim() === '' && (
                <CommandGroup>
                  <CommandItem
                    value="__start_create__"
                    onSelect={() => setNaming(true)}
                    className="cursor-pointer"
                  >
                    <PlusIcon className="size-4 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1 truncate">
                      {createsInGroup ? t('categorization.create.start') : `${t('categorization.create.naming')}…`}
                    </span>
                    <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground" />
                  </CommandItem>
                </CommandGroup>
              )}
              {allowNone && (
                <CommandGroup>
                  <CommandItem
                    value={`none ${t('transactions.noCategory')}`}
                    onSelect={() => {
                      onChange('')
                      handleOpenChange(false)
                    }}
                    className="italic text-muted-foreground cursor-pointer"
                  >
                    <span className="flex-1">{t('transactions.noCategory')}</span>
                    {value === '' && <CheckIcon className="size-4 shrink-0" />}
                  </CommandItem>
                </CommandGroup>
              )}
              {displayGroups.map((group) => (
                <CommandGroup key={group.id}>
                  <div className="px-2 py-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/70">
                    {group.name}
                  </div>
                  {group.categories.map((cat) => (
                    <CommandItem
                      key={cat.id}
                      value={`${group.name} ${cat.name}`}
                      onSelect={() => {
                        onChange(cat.id)
                        handleOpenChange(false)
                      }}
                      className="cursor-pointer"
                    >
                      <div className="flex items-center gap-2 min-w-0 truncate flex-1">
                        {cat.color ? (
                          <span
                            className="size-2.5 shrink-0 rounded-full border border-black/5"
                            style={{ backgroundColor: cat.color }}
                          />
                        ) : null}
                        <span className="truncate">{cat.name}</span>
                      </div>
                      {value === cat.id && <CheckIcon className="size-4 shrink-0" />}
                    </CommandItem>
                  ))}
                </CommandGroup>
              ))}
              {createItems}
              {footerAction && (
                <CommandGroup forceMount className="border-t border-border">
                  <CommandItem
                    forceMount
                    value={`__footer__ ${footerAction.label}`}
                    disabled={footerAction.disabled}
                    title={footerAction.hint}
                    onSelect={() => {
                      handleOpenChange(false)
                      footerAction.onSelect()
                    }}
                    className="cursor-pointer"
                  >
                    {footerAction.icon && (
                      <footerAction.icon className="size-4 shrink-0 text-muted-foreground" />
                    )}
                    <span className="truncate">{footerAction.label}</span>
                  </CommandItem>
                </CommandGroup>
              )}
            </CommandList>
          )}
        </Command>
      </PopoverContent>
    </Popover>
  )
}
