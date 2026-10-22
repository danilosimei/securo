import { useMemo, useState } from 'react'
import { FormDialogFooter } from '@/components/form-dialog-footer'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { ListSkeleton } from '@/components/ui/list-skeleton'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { categories as categoriesApi, categoryGroups as groupsApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import type { Category, CategoryGroup, CategoryUsage, ReportKind } from '@/types'
import { Tags, Trash2, Plus, ChevronDown, ChevronRight, ChevronsUpDown, Eye, EyeOff, WandSparkles } from 'lucide-react'
import { RowActions } from '@/components/ui/row-actions'
import { PageActions } from '@/components/page-actions'
import { PageHeader } from '@/components/page-header'
import { invalidateCategoryQueries } from '@/lib/invalidate-queries'
import { CategoryIcon } from '@/components/category-icon'
import { CategorySelect } from '@/components/category-select'
import { IconPicker } from '@/components/icon-picker'
import { useWorkspace } from '@/contexts/workspace-context'
import { SectionCard, SectionHeader } from '@/components/ui/section-card'

const DESCRIPTION_MAX = 300
const REPORT_KINDS: { value: ReportKind; label: string; help: string }[] = [
  { value: 'normal', label: 'categories.reportKindNormal', help: 'categories.reportKindNormalHelp' },
  { value: 'income', label: 'categories.reportKindIncome', help: 'categories.reportKindIncomeHelp' },
  { value: 'refund', label: 'categories.reportKindRefund', help: 'categories.reportKindRefundHelp' },
]

export default function CategoriesPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { canWrite } = useWorkspace()
  const navigate = useNavigate()
  const [catDialogOpen, setCatDialogOpen] = useState(false)
  const [editingCat, setEditingCat] = useState<Category | null>(null)
  const [formIcon, setFormIcon] = useState('circle-help')
  const [formColor, setFormColor] = useState('#6366f1')
  const [formTreatAsTransfer, setFormTreatAsTransfer] = useState(false)
  const [formIgnoreTransfer, setFormIgnoreTransfer] = useState(false)
  const [formReportKind, setFormReportKind] = useState<ReportKind>('normal')
  const [formDescription, setFormDescription] = useState('')
  const [groupDialogOpen, setGroupDialogOpen] = useState(false)
  const [editingGroup, setEditingGroup] = useState<CategoryGroup | null>(null)
  const [groupFormIcon, setGroupFormIcon] = useState('circle-help')
  const [groupFormColor, setGroupFormColor] = useState('#6B7280')
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set())
  const [deletingCategory, setDeletingCategory] = useState<Category | null>(null)
  const [deletingGroup, setDeletingGroup] = useState<CategoryGroup | null>(null)
  const [hidingCategory, setHidingCategory] = useState<
    { category: Category; rules: { id: string; name: string }[] } | null
  >(null)
  // Set when the category being deleted still holds something. The deletion
  // then waits for a destination instead of going through.
  const [transferringCategory, setTransferringCategory] = useState<
    { category: Category; usage: CategoryUsage } | null
  >(null)
  const [transferTargetId, setTransferTargetId] = useState('')

  const { data: groups, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['category-groups', 'management'],
    queryFn: groupsApi.listIncludingHidden,
  })

  const { data: categoriesList } = useQuery({
    queryKey: ['categories', 'management'],
    queryFn: categoriesApi.listIncludingHidden,
  })

  const invalidateAll = () => {
    invalidateCategoryQueries(queryClient)
  }

  const createCatMutation = useMutation({
    mutationFn: (cat: Partial<Category>) => categoriesApi.create(cat),
    onSuccess: () => { invalidateAll(); setCatDialogOpen(false); toast.success(t('categories.created')) },
  })
  const updateCatMutation = useMutation({
    mutationFn: ({ id, ...data }: Partial<Category> & { id: string }) => categoriesApi.update(id, data),
    onSuccess: () => { invalidateAll(); setCatDialogOpen(false); setEditingCat(null); toast.success(t('categories.updated')) },
  })

  // Hiding is its own mutation: it can also retire the rules that assign the
  // category, and it must refresh the rule list rather than the category form.
  const hideCatMutation = useMutation({
    mutationFn: ({ id, deactivateRules }: { id: string; deactivateRules: boolean }) =>
      categoriesApi.update(id, { is_hidden: true }, { deactivateRules }),
    onSuccess: (_data, variables) => {
      invalidateAll()
      if (variables.deactivateRules) queryClient.invalidateQueries({ queryKey: ['rules'] })
      setHidingCategory(null)
      toast.success(t('categories.updated'))
    },
    onError: (err: unknown) => toast.error(extractApiError(err, t('common.error'))),
  })

  // Rules that still file transactions into a category outlive hiding it, so
  // check for them first and only interrupt the user when there are any.
  async function handleToggleHidden(cat: Category) {
    if (cat.is_hidden) {
      updateCatMutation.mutate({ id: cat.id, is_hidden: false })
      return
    }
    try {
      const usage = await queryClient.fetchQuery({
        queryKey: ['category-rule-usage', cat.id],
        queryFn: () => categoriesApi.ruleUsage(cat.id),
        staleTime: 0,
      })
      if (usage.rules.length === 0) {
        hideCatMutation.mutate({ id: cat.id, deactivateRules: false })
        return
      }
      setHidingCategory({ category: cat, rules: usage.rules })
    } catch (err) {
      toast.error(extractApiError(err, t('common.error')))
    }
  }
  const deleteCatMutation = useMutation({
    mutationFn: ({ id, transferToId }: { id: string; transferToId?: string }) =>
      categoriesApi.delete(id, transferToId),
    onSuccess: (_data, variables) => {
      invalidateAll()
      setDeletingCategory(null)
      setTransferringCategory(null)
      // A transfer rewrites transactions, budgets, recurring entries and
      // rules, so the screens built on them are all stale now.
      if (variables.transferToId) {
        for (const key of ['transactions', 'budgets', 'recurring-transactions', 'rules']) {
          queryClient.invalidateQueries({ queryKey: [key] })
        }
      }
      toast.success(t('categories.deleted'))
    },
    onError: (err: unknown) => {
      // The API answers 409 with an English sentence; show the translated one instead.
      if (axios.isAxiosError(err) && err.response?.status === 409) {
        toast.error(t('categories.deleteInUse'))
        return
      }
      toast.error(extractApiError(err, t('common.error')))
    },
  })

  // Deleting used to be refused whenever anything pointed at the category.
  // Now the references are counted first, and the user is only interrupted
  // when there is something to decide.
  async function handleDeleteCategory(cat: Category) {
    try {
      const usage = await queryClient.fetchQuery({
        queryKey: ['category-usage', cat.id],
        queryFn: () => categoriesApi.usage(cat.id),
        staleTime: 0,
      })
      const isUsed =
        usage.transactions > 0
        || usage.budgets > 0
        || usage.recurring_transactions > 0
        || usage.rules.length > 0
      if (!isUsed) {
        setDeletingCategory(cat)
        return
      }
      setTransferTargetId('')
      setTransferringCategory({ category: cat, usage })
    } catch (err) {
      toast.error(extractApiError(err, t('common.error')))
    }
  }

  // What the destination picker offers: every visible category except the one
  // on its way out, kept in its groups so it reads like the pickers elsewhere.
  const transferOptions = useMemo(() => {
    const sourceId = transferringCategory?.category.id
    const available = (categoriesList ?? []).filter(
      (cat) => !cat.is_hidden && cat.id !== sourceId,
    )
    const availableIds = new Set(available.map((cat) => cat.id))
    return {
      categories: available,
      groups: (groups ?? [])
        .filter((group) => !group.is_hidden)
        .map((group) => ({
          ...group,
          categories: (group.categories ?? []).filter((cat) => availableIds.has(cat.id)),
        }))
        .filter((group) => group.categories.length > 0),
    }
  }, [categoriesList, groups, transferringCategory])

  const createGroupMutation = useMutation({
    mutationFn: (g: Partial<CategoryGroup>) => groupsApi.create(g),
    onSuccess: () => { invalidateAll(); setGroupDialogOpen(false); toast.success(t('groups.created')) },
  })
  const updateGroupMutation = useMutation({
    mutationFn: ({ id, ...data }: Partial<CategoryGroup> & { id: string }) => groupsApi.update(id, data),
    onSuccess: () => { invalidateAll(); setGroupDialogOpen(false); setEditingGroup(null); toast.success(t('groups.updated')) },
    onError: () => { toast.error(t('common.error')) },
  })
  const deleteGroupMutation = useMutation({
    mutationFn: (id: string) => groupsApi.delete(id),
    onSuccess: () => { invalidateAll(); setDeletingGroup(null); toast.success(t('groups.deleted')) },
    onError: (err: unknown) => {
      toast.error(extractApiError(err, t('common.error')))
    },
  })

  const toggleCollapse = (groupId: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(groupId)) next.delete(groupId)
      else next.add(groupId)
      return next
    })
  }

  const ungrouped = categoriesList?.filter((c) => !c.group_id) ?? []

  const openCatDialog = (cat: Category | null) => {
    setEditingCat(cat)
    setFormIcon(cat?.icon ?? 'circle-help')
    setFormColor(cat?.color ?? '#6366f1')
    setFormTreatAsTransfer(cat?.treat_as_transfer ?? false)
    setFormIgnoreTransfer(cat?.is_ignored ?? false)
    setFormReportKind(cat?.report_kind ?? 'normal')
    setFormDescription(cat?.description ?? '')
    setCatDialogOpen(true)
  }

  const openGroupDialog = (group: CategoryGroup | null) => {
    setEditingGroup(group)
    setGroupFormIcon(group?.icon ?? 'circle-help')
    setGroupFormColor(group?.color ?? '#6B7280')
    setGroupDialogOpen(true)
  }

  const renderHiddenBadge = (label: string) => (
    <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-muted text-muted-foreground border border-border shrink-0">
      {label}
    </span>
  )

  const renderCategoryItem = (cat: Category) => (
    <div key={cat.id} className={`flex items-center gap-3 px-4 sm:px-5 pl-6 sm:pl-12 py-2.5 border-b border-border last:border-0 hover:bg-muted transition-colors ${cat.is_hidden ? 'opacity-60' : ''}`}>
      <CategoryIcon icon={cat.icon} color={cat.color} size="md" />
      <div className="flex-1 min-w-0 flex items-center gap-2">
        <span className="text-sm font-medium text-foreground truncate">{cat.name}</span>
        {cat.is_hidden && renderHiddenBadge(t('categories.hiddenBadge'))}
        {cat.treat_as_transfer && (
          <span
            className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-muted text-muted-foreground border border-border shrink-0"
            title={t('categories.treatAsTransferDesc')}
          >
            {t('categories.treatAsTransferBadge')}
          </span>
        )}
        {cat.is_ignored && (
          <span
            className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-muted text-muted-foreground border border-border shrink-0"
            title={t('categories.ignoreTransferDesc')}
          >
            {t('categories.ignoreTransferBadge')}
          </span>
        )}
      </div>
      <div className="hidden sm:flex items-center gap-2 shrink-0">
        <span className="inline-block w-3.5 h-3.5 rounded-full border border-black/10" style={{ backgroundColor: cat.color }} />
        <span className="text-xs text-muted-foreground font-mono">{cat.color}</span>
      </div>
      {canWrite && (
        <div className="shrink-0 ml-2">
          <RowActions
            label={cat.name}
            onEdit={() => openCatDialog(cat)}
            onDelete={() => handleDeleteCategory(cat)}
            deleteDisabled={deleteCatMutation.isPending}
            extra={
              cat.is_system
                ? [{
                    label: cat.is_hidden ? t('categories.showDefault') : t('categories.hideDefault'),
                    icon: cat.is_hidden ? Eye : EyeOff,
                    onSelect: () => handleToggleHidden(cat),
                    disabled: updateCatMutation.isPending || hideCatMutation.isPending,
                  }]
                : []
            }
          />
        </div>
      )}
    </div>
  )

  return (
    <div>
      <PageHeader
        section={t('nav.groupOrganization')}
        title={t('categories.title')}
        action={
          <PageActions
            primary={
              canWrite ? (
                <Button onClick={() => openCatDialog(null)}>
                  <Plus /> {t('categories.addCategory')}
                </Button>
              ) : undefined
            }
            items={[
              { label: t('categories.organizeWithAi'), icon: WandSparkles, onSelect: () => navigate('/categorization?tab=organize') },
              ...(canWrite ? [{ label: t('groups.add'), icon: Plus, onSelect: () => openGroupDialog(null) }] : []),
            ]}
          />
        }
      />

      <p className="mb-4 text-sm text-muted-foreground">
        {t('categories.hiddenScopeDescription')}
      </p>

      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : (
      <SectionCard>
        <SectionHeader
          title={t('categories.title')}
          titleExtra={
            <button
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => {
                if (!groups) return
                const allCollapsed = groups.every((g) => collapsedGroups.has(g.id))
                if (allCollapsed) {
                  setCollapsedGroups(new Set())
                } else {
                  setCollapsedGroups(new Set(groups.map((g) => g.id)))
                }
              }}
            >
              <ChevronsUpDown size={13} />
              {groups && groups.every((g) => collapsedGroups.has(g.id)) ? t('categories.expandAll') : t('categories.collapseAll')}
            </button>
          }
        />
        {isLoading ? (
          <ListSkeleton />
        ) : (groups?.length ?? 0) === 0 && ungrouped.length === 0 ? (
          <EmptyState
            icon={Tags}
            title={t('categories.empty')}
            action={
              canWrite ? (
                <Button onClick={() => openCatDialog(null)}>
                  <Plus /> {t('categories.addCategory')}
                </Button>
              ) : undefined
            }
          />
        ) : (
        <div>
          {groups?.map((group) => {
            const isCollapsed = collapsedGroups.has(group.id)
            return (
              <div key={group.id} className={group.is_hidden ? 'opacity-60' : ''}>
                <div className="flex items-center gap-2 px-4 sm:px-5 py-3 border-b border-border bg-muted/40">
                  <button
                    className="flex items-center gap-2 flex-1 min-w-0 text-left"
                    onClick={() => toggleCollapse(group.id)}
                  >
                    {isCollapsed ? <ChevronRight size={14} className="text-muted-foreground shrink-0" /> : <ChevronDown size={14} className="text-muted-foreground shrink-0" />}
                    <CategoryIcon icon={group.icon} color={group.color} size="md" />
                    <span className="text-sm font-semibold" style={{ color: group.color }}>{group.name}</span>
                    {group.is_hidden && renderHiddenBadge(t('groups.hiddenBadge'))}
                    <span className="text-xs text-muted-foreground">({group.categories.length})</span>
                  </button>
                  {canWrite && (
                    <div className="shrink-0">
                      <RowActions
                        label={group.name}
                        onEdit={() => openGroupDialog(group)}
                        onDelete={() => setDeletingGroup(group)}
                        deleteDisabled={deleteGroupMutation.isPending}
                        extra={
                          group.is_system
                            ? [{
                                label: group.is_hidden ? t('groups.showDefault') : t('groups.hideDefault'),
                                icon: group.is_hidden ? Eye : EyeOff,
                                onSelect: () => updateGroupMutation.mutate({ id: group.id, is_hidden: !group.is_hidden }),
                                disabled: updateGroupMutation.isPending,
                              }]
                            : []
                        }
                      />
                    </div>
                  )}
                </div>
                {!isCollapsed && group.categories.map(renderCategoryItem)}
              </div>
            )
          })}
          {ungrouped.length > 0 && (
            <div>
              <div className="px-5 py-3 border-b border-border bg-muted/40">
                <span className="text-sm font-semibold text-muted-foreground">{t('groups.noGroup')}</span>
              </div>
              {ungrouped.map(renderCategoryItem)}
            </div>
          )}
        </div>
        )}
      </SectionCard>
      )}

      {/* Category Dialog */}
      <Dialog open={catDialogOpen} onOpenChange={() => { setCatDialogOpen(false); setEditingCat(null) }}>
        <DialogContent className="flex flex-col overflow-hidden">
          <DialogHeader>
            <DialogTitle>{editingCat ? t('categories.editCategory') : t('categories.newCategory')}</DialogTitle>
          </DialogHeader>
          <form
            key={editingCat?.id ?? 'new'}
            onSubmit={(e) => {
              e.preventDefault()
              const formData = new FormData(e.currentTarget)
              const data = {
                name: formData.get('name') as string,
                icon: formData.get('icon') as string,
                color: formData.get('color') as string,
                group_id: (formData.get('group_id') as string) || null,
                treat_as_transfer: formTreatAsTransfer,
                is_ignored: formIgnoreTransfer,
                report_kind: formReportKind,
                description: formDescription.trim() || null
              }
              if (editingCat) {
                updateCatMutation.mutate({ id: editingCat.id, ...data })
              } else {
                createCatMutation.mutate(data)
              }
            }}
            className="flex min-h-0 flex-1 flex-col"
          >
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-3">
              <div className="space-y-2">
                <Label>{t('groups.name')}</Label>
                <Input name="name" defaultValue={editingCat?.name ?? ''} required />
              </div>
              <div className="space-y-2">
                <Label>{t('categories.group')}</Label>
                <select
                  name="group_id"
                  defaultValue={editingCat?.group_id ?? ''}
                  className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                >
                  <option value="">{t('categories.noGroup')}</option>
                  {groups?.filter((g) => !g.is_hidden || g.id === editingCat?.group_id).map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name}{g.is_hidden ? ` (${t('groups.hiddenBadge')})` : ''}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-2">
                <Label>{t('groups.color')}</Label>
                <Input name="color" type="color" value={formColor} onChange={(e) => setFormColor(e.target.value)} required className="h-9 px-2 py-1" />
              </div>
              <div className="space-y-2">
                <Label>{t('groups.icon')}</Label>
                <IconPicker value={formIcon} color={formColor} onChange={setFormIcon} />
                <input type="hidden" name="icon" value={formIcon} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="category-report-kind">{t('categories.reportKind')}</Label>
                <select
                  id="category-report-kind"
                  value={formReportKind}
                  onChange={(e) => setFormReportKind(e.target.value as ReportKind)}
                  className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                >
                  {REPORT_KINDS.map((kind) => (
                    <option key={kind.value} value={kind.value}>{t(kind.label)}</option>
                  ))}
                </select>
                <p className="text-xs text-muted-foreground">
                  {t(REPORT_KINDS.find((kind) => kind.value === formReportKind)!.help)}
                </p>
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <Label htmlFor="category-description">{t('categories.descriptionLabel')}</Label>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {formDescription.length}/{DESCRIPTION_MAX}
                  </span>
                </div>
                <textarea
                  id="category-description"
                  value={formDescription}
                  onChange={(e) => setFormDescription(e.target.value)}
                  maxLength={DESCRIPTION_MAX}
                  rows={3}
                  placeholder={t('categories.descriptionPlaceholder')}
                  className="w-full resize-y border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                />
              </div>
              <div className="pt-2 border-t border-border">
                <label className="flex items-start gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={formTreatAsTransfer}
                    onChange={(e) => setFormTreatAsTransfer(e.target.checked)}
                    className="h-4 w-4 mt-0.5 rounded border-border shrink-0"
                  />
                  <div className="flex-1 min-w-0">
                    <span className="text-sm font-medium text-foreground">{t('categories.treatAsTransfer')}</span>
                    <p className="text-xs text-muted-foreground mt-0.5">{t('categories.treatAsTransferDesc')}</p>
                  </div>
                </label>
                <label className="flex items-start gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={formIgnoreTransfer}
                    onChange={(e) => setFormIgnoreTransfer(e.target.checked)}
                    className="h-4 w-4 mt-0.5 rounded border-border shrink-0"
                  />
                  <div className="flex-1 min-w-0">
                    <span className="text-sm font-medium text-foreground">{t('categories.ignoreTransfer')}</span>
                    <p className="text-xs text-muted-foreground mt-0.5">{t('categories.ignoreTransferDesc')}</p>
                  </div>
                </label>
              </div>
            </div>
            <FormDialogFooter
              className="mt-2 shrink-0 border-t pt-4"
              onCancel={() => { setCatDialogOpen(false); setEditingCat(null) }}
              onDelete={
                editingCat
                  ? () => {
                      const category = editingCat
                      setCatDialogOpen(false)
                      setEditingCat(null)
                      handleDeleteCategory(category)
                    }
                  : undefined
              }
            />
          </form>
        </DialogContent>
      </Dialog>

      {/* Group Dialog */}
      <Dialog open={groupDialogOpen} onOpenChange={() => { setGroupDialogOpen(false); setEditingGroup(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editingGroup ? t('groups.edit') : t('groups.new')}</DialogTitle>
          </DialogHeader>
          <form
            key={editingGroup?.id ?? 'new-group'}
            onSubmit={(e) => {
              e.preventDefault()
              const formData = new FormData(e.currentTarget)
              const data = {
                name: formData.get('name') as string,
                icon: formData.get('icon') as string,
                color: formData.get('color') as string,
                position: parseInt(formData.get('position') as string) || 0,
              }
              if (editingGroup) {
                updateGroupMutation.mutate({ id: editingGroup.id, ...data })
              } else {
                createGroupMutation.mutate(data)
              }
            }}
            className="space-y-4"
          >
            <div className="space-y-2">
              <Label>{t('groups.name')}</Label>
              <Input name="name" defaultValue={editingGroup?.name ?? ''} required />
            </div>
            <div className="space-y-2">
              <Label>{t('groups.position')}</Label>
              <Input name="position" type="number" defaultValue={editingGroup?.position?.toString() ?? '0'} />
            </div>
            <div className="space-y-2">
              <Label>{t('groups.color')}</Label>
              <Input name="color" type="color" value={groupFormColor} onChange={(e) => setGroupFormColor(e.target.value)} required className="h-9 px-2 py-1" />
            </div>
            <div className="space-y-2">
              <Label>{t('groups.icon')}</Label>
              <IconPicker value={groupFormIcon} color={groupFormColor} onChange={setGroupFormIcon} />
              <input type="hidden" name="icon" value={groupFormIcon} />
            </div>
            <FormDialogFooter
              onCancel={() => { setGroupDialogOpen(false); setEditingGroup(null) }}
              onDelete={
                editingGroup
                  ? () => {
                      const group = editingGroup
                      setGroupDialogOpen(false)
                      setEditingGroup(null)
                      setDeletingGroup(group)
                    }
                  : undefined
              }
            />
          </form>
        </DialogContent>
      </Dialog>

      <DeleteConfirmationDialog
        open={!!deletingCategory}
        title={t('categories.confirmDeleteTitle')}
        description={t('categories.confirmDeleteDescription', { name: deletingCategory?.name })}
        isPending={deleteCatMutation.isPending}
        onClose={() => setDeletingCategory(null)}
        onConfirm={() =>
          deletingCategory && deleteCatMutation.mutate({ id: deletingCategory.id })
        }
      />

      <DeleteConfirmationDialog
        open={!!deletingGroup}
        title={t('groups.confirmDeleteTitle')}
        description={t('groups.confirmDeleteDescription', { name: deletingGroup?.name })}
        isPending={deleteGroupMutation.isPending}
        onClose={() => setDeletingGroup(null)}
        onConfirm={() => deletingGroup && deleteGroupMutation.mutate(deletingGroup.id)}
      />

      <Dialog
        open={!!transferringCategory}
        onOpenChange={(open) => {
          if (!open && !deleteCatMutation.isPending) setTransferringCategory(null)
        }}
      >
        <DialogContent size="sm" showCloseButton={!deleteCatMutation.isPending}>
          <DialogHeader>
            <DialogTitle>
              {t('categories.deleteInUseTitle', { name: transferringCategory?.category.name })}
            </DialogTitle>
            <DialogDescription>{t('categories.deleteInUseDescription')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <ul className="rounded-lg border border-border divide-y divide-border">
              {([
                ['nav.transactions', transferringCategory?.usage.transactions ?? 0],
                ['nav.budgets', transferringCategory?.usage.budgets ?? 0],
                ['nav.recurring', transferringCategory?.usage.recurring_transactions ?? 0],
                ['nav.rules', transferringCategory?.usage.rules.length ?? 0],
              ] as const)
                .filter(([, count]) => count > 0)
                .map(([label, count]) => (
                  <li
                    key={label}
                    className="flex items-center justify-between px-3 py-2 text-sm"
                  >
                    <span className="text-muted-foreground">{t(label)}</span>
                    <span className="font-medium text-foreground tabular-nums">{count}</span>
                  </li>
                ))}
            </ul>
            <div className="space-y-2">
              <Label>{t('categories.deleteTransferLabel')}</Label>
              <CategorySelect
                value={transferTargetId}
                onChange={setTransferTargetId}
                categories={transferOptions.categories}
                groups={transferOptions.groups}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setTransferringCategory(null)}
              disabled={deleteCatMutation.isPending}
            >
              {t('common.cancel')}
            </Button>
            <Button
              variant="destructive"
              disabled={!transferTargetId || deleteCatMutation.isPending}
              onClick={() =>
                transferringCategory
                && deleteCatMutation.mutate({
                  id: transferringCategory.category.id,
                  transferToId: transferTargetId,
                })
              }
            >
              <Trash2 size={14} className="mr-1" />
              {t('categories.deleteAndTransfer')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!hidingCategory} onOpenChange={(open) => !open && setHidingCategory(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t('categories.hideWithRulesTitle', { name: hidingCategory?.category.name })}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              {t('categories.hideWithRulesDescription', { count: hidingCategory?.rules.length ?? 0 })}
            </p>
            <ul className="max-h-40 overflow-y-auto rounded-lg border border-border divide-y divide-border">
              {hidingCategory?.rules.map((rule) => (
                <li key={rule.id} className="px-3 py-2 text-sm text-foreground truncate">
                  {rule.name}
                </li>
              ))}
            </ul>
          </div>
          <DialogFooter className="gap-2 sm:justify-between">
            <Button
              variant="outline"
              onClick={() => setHidingCategory(null)}
              disabled={hideCatMutation.isPending}
            >
              {t('common.cancel')}
            </Button>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                onClick={() =>
                  hidingCategory
                  && hideCatMutation.mutate({ id: hidingCategory.category.id, deactivateRules: false })
                }
                disabled={hideCatMutation.isPending}
              >
                {t('categories.hideKeepRules')}
              </Button>
              <Button
                onClick={() =>
                  hidingCategory
                  && hideCatMutation.mutate({ id: hidingCategory.category.id, deactivateRules: true })
                }
                disabled={hideCatMutation.isPending}
              >
                {t('categories.hideAndTurnOffRules')}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
