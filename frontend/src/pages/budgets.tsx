import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { categories as categoriesApi, categoryGroups as groupsApi, budgets as budgetsApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import type { Budget } from '@/types'
import { Plus, Repeat } from 'lucide-react'
import { RowActions } from '@/components/ui/row-actions'
import { PageHeader } from '@/components/page-header'
import { MonthPeriodSelector } from '@/components/reports/period-selector'
import { CategoryIcon } from '@/components/category-icon'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { findCategoryReference } from '@/lib/category-reference-utils'
import { formatCurrency } from '@/lib/format'
import { SectionCard, SectionHeader, TH } from '@/components/ui/section-card'

function currentMonth() {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

export default function BudgetsPage() {
  const { t, i18n } = useTranslation()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const queryClient = useQueryClient()
  const [selectedMonth, setSelectedMonth] = useState(currentMonth)
  const monthParam = `${selectedMonth}-01`
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Budget | null>(null)
  const [deletingBudget, setDeletingBudget] = useState<Budget | null>(null)

  const { data: budgetsList } = useQuery({
    queryKey: ['budgets', selectedMonth],
    queryFn: () => budgetsApi.list(monthParam),
  })

  const { data: categoriesList } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })

  // Budgets can point at a hidden default category. The picker below only
  // offers visible ones, but existing rows still have to name what they budget.
  const { data: allCategoriesList } = useQuery({
    queryKey: ['categories', 'management'],
    queryFn: categoriesApi.listIncludingHidden,
  })

  const { data: groupsList } = useQuery({
    queryKey: ['category-groups'],
    queryFn: groupsApi.list,
  })

  const createMutation = useMutation({
    mutationFn: (data: { category_id: string; amount: number; month: string; is_recurring?: boolean }) =>
      budgetsApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      setDialogOpen(false)
      toast.success(t('budgets.created'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, amount }: { id: string; amount: number }) =>
      budgetsApi.update(id, { amount }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      setDialogOpen(false)
      setEditing(null)
      toast.success(t('budgets.updated'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => budgetsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      setDeletingBudget(null)
      toast.success(t('budgets.deleted'))
    },
    onError: (err: unknown) => {
      toast.error(extractApiError(err, t('common.error')))
    },
  })

  const displayCategories = allCategoriesList ?? categoriesList ?? []

  const getCategoryDisplay = (categoryId: string) => {
    const category = findCategoryReference(displayCategories, categoryId)
    if (!category) return <span>{categoryId}</span>
    return (
      <span className="flex items-center gap-2">
        <CategoryIcon icon={category.icon} color={category.color} size="sm" />
        <span>{category.name}</span>
      </span>
    )
  }

  const uiLocale = i18n.resolvedLanguage ?? i18n.language

  return (
    <div>
      <PageHeader
        section={t('nav.groupPlanning')}
        title={t('budgets.title')}
        action={
          canWrite ? (
            <Button onClick={() => { setEditing(null); setDialogOpen(true) }}>
              <Plus /> {t('budgets.add')}
            </Button>
          ) : undefined
        }
        period={<MonthPeriodSelector value={selectedMonth} onChange={setSelectedMonth} locale={uiLocale} />}
      />

      <SectionCard>
        <SectionHeader title={t('budgets.title')} />
        {budgetsList && budgetsList.length > 0 ? (
          <table className="w-full">
            <thead>
              <tr className="border-b border-border">
                <th className={`${TH} pl-4 sm:pl-5 text-left`}>{t('budgets.category')}</th>
                <th className={`${TH} text-left w-36`}>{t('budgets.amount')}</th>
                {canWrite && <th className={`${TH} pr-4 sm:pr-5 text-right w-16`}>{t('budgets.actions')}</th>}
              </tr>
            </thead>
            <tbody>
              {budgetsList.map((budget) => (
                <tr key={budget.id} className="border-b border-border last:border-0 hover:bg-muted transition-colors">
                  <td className="py-3 pl-4 sm:pl-5 text-sm font-medium text-foreground">
                    <span className="flex items-center gap-1.5">
                      {getCategoryDisplay(budget.category_id)}
                      {budget.is_recurring && (
                        <span title={t('budgets.recurringLabel')} className="text-muted-foreground">
                          <Repeat size={12} />
                        </span>
                      )}
                    </span>
                  </td>
                  <td className="py-3 text-sm font-semibold tabular-nums text-foreground">{mask(formatCurrency(budget.amount, userCurrency, locale))}</td>
                  {canWrite && (
                    <td className="py-3 pr-4 sm:pr-5">
                      <div className="flex items-center justify-end">
                        <RowActions
                          label={findCategoryReference(displayCategories, budget.category_id)?.name ?? t('budgets.category')}
                          onEdit={() => { setEditing(budget); setDialogOpen(true) }}
                          onDelete={() => setDeletingBudget(budget)}
                          deleteDisabled={deleteMutation.isPending}
                        />
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-muted-foreground text-center py-10">{t('budgets.empty')}</p>
        )}
      </SectionCard>

      <Dialog open={dialogOpen} onOpenChange={() => { setDialogOpen(false); setEditing(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? t('budgets.edit') : t('budgets.add')}</DialogTitle>
          </DialogHeader>
          <form
            key={editing?.id ?? 'new'}
            onSubmit={(e) => {
              e.preventDefault()
              const formData = new FormData(e.currentTarget)
              if (editing) {
                updateMutation.mutate({
                  id: editing.id,
                  amount: parseFloat(formData.get('amount') as string),
                })
              } else {
                const isRecurring = formData.get('is_recurring') === 'on'
                createMutation.mutate({
                  category_id: formData.get('category_id') as string,
                  amount: parseFloat(formData.get('amount') as string),
                  month: monthParam,
                  is_recurring: isRecurring,
                })
              }
            }}
            className="space-y-4"
          >
            {!editing && (
              <>
                <div className="space-y-2">
                  <Label>{t('budgets.category')}</Label>
                  <select
                    name="category_id"
                    className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                    required
                  >
                    <option value="">{t('budgets.selectCategory')}</option>
                    {groupsList?.map((group) => (
                      <optgroup key={group.id} label={group.name}>
                        {group.categories.map((cat) => (
                          <option key={cat.id} value={cat.id}>{cat.name}</option>
                        ))}
                      </optgroup>
                    ))}
                    {categoriesList?.filter((c) => !c.group_id).map((cat) => (
                      <option key={cat.id} value={cat.id}>{cat.name}</option>
                    ))}
                  </select>
                </div>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" name="is_recurring" className="rounded border-border" />
                  <span className="text-sm text-foreground">{t('budgets.repeatEveryMonth')}</span>
                </label>
              </>
            )}
            <div className="space-y-2">
              <Label>{t('budgets.amount')}</Label>
              <Input
                name="amount"
                type="number"
                step="0.01"
                defaultValue={editing?.amount?.toString() ?? ''}
                required
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => { setDialogOpen(false); setEditing(null) }}>
                {t('common.cancel')}
              </Button>
              <Button type="submit" disabled={createMutation.isPending || updateMutation.isPending}>
                {t('common.save')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <DeleteConfirmationDialog
        open={!!deletingBudget}
        title={t('budgets.confirmDeleteTitle')}
        description={t(
          deletingBudget?.is_recurring
            ? 'budgets.confirmDeleteRecurringDescription'
            : 'budgets.confirmDeleteDescription',
          {
            name: findCategoryReference(displayCategories, deletingBudget?.category_id ?? '')?.name
              ?? t('budgets.category'),
          },
        )}
        isPending={deleteMutation.isPending}
        onClose={() => setDeletingBudget(null)}
        onConfirm={() => deletingBudget && deleteMutation.mutate(deletingBudget.id)}
      />
    </div>
  )
}
