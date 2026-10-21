/** The transactions behind one store in Explore: paged list, the server's
 *  total, and the usual edit dialog on click. */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ChevronLeft, ChevronRight, ExternalLink } from 'lucide-react'
import {
  accounts as accountsApi,
  categories as categoriesApi,
  categoryGroups as categoryGroupsApi,
  transactions,
} from '@/lib/api'
import { getAccountName } from '@/lib/account-utils'
import { extractApiError } from '@/lib/api-errors'
import { formatCurrency } from '@/lib/format'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { cn } from '@/lib/utils'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { TransactionDialog, type TransactionSavePayload } from '@/components/transaction-dialog'
import {
  DRILLDOWN_PAGE_SIZE,
  drilldownTransactionParams,
  transactionsPageLink,
} from '@/components/reports/explore/drilldown'
import type { Transaction } from '@/types'

interface MerchantTransactionsProps {
  merchantLabel: string
  merchantKey: string
  /** Category id, or 'none'. */
  categoryId: string
  range: { from: string; to: string }
}

export function MerchantTransactions({ merchantLabel, merchantKey, categoryId, range }: MerchantTransactionsProps) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [editing, setEditing] = useState<Transaction | null>(null)

  const params = drilldownTransactionParams({ range, categoryId, merchantKey, page })
  const query = useQuery({
    queryKey: ['transactions', 'explore-merchant', params],
    queryFn: () => transactions.list(params),
    placeholderData: keepPreviousData,
  })
  const data = query.data

  const { data: categoriesList } = useQuery({ queryKey: ['categories'], queryFn: categoriesApi.list })
  const { data: categoryGroupsList } = useQuery({ queryKey: ['categoryGroups'], queryFn: categoryGroupsApi.list })
  const { data: accountsList } = useQuery({ queryKey: ['accounts'], queryFn: () => accountsApi.list() })

  const close = () => setEditing(null)
  const done = () => {
    invalidateFinancialQueries(queryClient)
    close()
  }
  const updateMutation = useMutation({
    mutationFn: ({ id, ...body }: TransactionSavePayload & { id: string }) => transactions.update(id, body),
    onSuccess: done,
  })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => transactions.delete(id),
    onSuccess: done,
  })
  const unlinkMutation = useMutation({
    mutationFn: (pairId: string) => transactions.unlinkTransfer(pairId),
    onSuccess: done,
  })

  const pages = data ? Math.max(1, Math.ceil(data.total / DRILLDOWN_PAGE_SIZE)) : 1
  // Same rule as the report: refunds in a category reduce its spending, but
  // credits without a category are income to check, not a reduction.
  const spent = data?.summary
    ? categoryId === 'none'
      ? data.summary.expense
      : data.summary.expense - data.summary.income
    : null

  return (
    <section className="space-y-3" aria-busy={query.isPlaceholderData}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">
          {t('reports.explore.txTitle', { merchant: merchantLabel, total: data?.total ?? 0 })}
          {data?.summary && spent !== null && (
            <span className="ml-2 font-normal tabular-nums text-muted-foreground">
              {mask(formatCurrency(spent, data.summary.currency, locale))}
            </span>
          )}
        </h3>
        <Link
          to={transactionsPageLink({ range, categoryId, merchantKey })}
          className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
        >
          {t('reports.explore.openInTransactions')}
          <ExternalLink size={12} aria-hidden="true" />
        </Link>
      </div>

      {query.isError ? (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-2.5 text-sm text-rose-900 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
        >
          {t('reports.explore.txError')}
          <Button type="button" variant="ghost" size="sm" onClick={() => query.refetch()}>
            {t('reports.explore.retry')}
          </Button>
        </div>
      ) : !data ? (
        <div className="space-y-2" data-testid="explore-tx-skeleton">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
      ) : data.items.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">{t('reports.explore.txEmpty')}</p>
      ) : (
        <div
          className={cn(
            'rounded-xl border border-border bg-card shadow-sm transition-opacity',
            query.isPlaceholderData && 'opacity-60',
          )}
        >
          <ul className="divide-y divide-border">
            {data.items.map((tx) => {
              const value = tx.type === 'credit' ? 0 - Number(tx.amount) : Number(tx.amount)
              return (
                <li key={tx.id}>
                  <button
                    type="button"
                    onClick={() => setEditing(tx)}
                    className="flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm hover:bg-muted/50 sm:px-4"
                  >
                    <span className="w-24 shrink-0 text-xs tabular-nums text-muted-foreground">
                      {new Date(tx.date + 'T00:00:00').toLocaleDateString(locale)}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-foreground">{tx.description}</span>
                    <span className="shrink-0 tabular-nums">{mask(formatCurrency(value, tx.currency, locale))}</span>
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {data && pages > 1 && (
        <div className="flex items-center justify-end gap-2 text-xs text-muted-foreground">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={t('reports.explore.prevPage')}
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            <ChevronLeft size={16} />
          </Button>
          <span className="tabular-nums">{t('reports.explore.pageOf', { page, pages })}</span>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={t('reports.explore.nextPage')}
            disabled={page >= pages}
            onClick={() => setPage((p) => p + 1)}
          >
            <ChevronRight size={16} />
          </Button>
        </div>
      )}

      <TransactionDialog
        open={!!editing}
        onClose={close}
        transaction={editing}
        categories={categoriesList ?? []}
        categoryGroups={categoryGroupsList ?? []}
        accounts={(accountsList ?? []).map((a: { id: string; name: string; display_name?: string | null }) => ({
          id: a.id,
          name: getAccountName(a),
        }))}
        onSave={(body) => {
          if (editing) updateMutation.mutate({ id: editing.id, ...body })
        }}
        onDelete={() => {
          if (editing) deleteMutation.mutate(editing.id)
        }}
        onUnlinkTransfer={(pairId) => unlinkMutation.mutate(pairId)}
        loading={updateMutation.isPending || deleteMutation.isPending || unlinkMutation.isPending}
        error={
          updateMutation.error
            ? extractApiError(updateMutation.error)
            : deleteMutation.error
              ? extractApiError(deleteMutation.error)
              : null
        }
        isSynced={editing?.source === 'sync'}
      />
    </section>
  )
}
