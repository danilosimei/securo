import { useEffect, useRef, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { useQuery } from '@tanstack/react-query'
import { transactions as transactionsApi, dashboard, admin } from '@/lib/api'
import { AlertTriangle, ChevronLeft, ChevronRight, Info, Paperclip, X } from 'lucide-react'
import { CategoryIcon } from '@/components/category-icon'
import { CategorizationOriginBadge } from '@/components/categorization-origin-badge'
import { ProjectedTransactionBadge } from '@/components/projected-transaction-badge'
import { drillDownServerTotal } from '@/lib/drill-down-totals'
import { hasOpenDialog, isInsideOverlayLayer } from '@/lib/overlay-layers'
import { useAuth } from '@/contexts/auth-context'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import type { Transaction } from '@/types'
import { formatCurrency } from '@/lib/format'

export const DRILL_DOWN_PAGE_SIZE = 50

export type DrillDownFilter = {
  title: string
  category_id?: string
  uncategorized?: boolean
  account_id?: string
  // Scope to a set of accounts (e.g. the active collection's accounts).
  account_ids?: string[]
  type?: 'credit' | 'debit'
  from?: string
  to?: string
}

type DisplayItem = {
  key: string
  description: string
  date: string
  type: 'debit' | 'credit'
  amount: number
  amountPrimary: number | null
  currency: string
  categoryIcon: string | null
  categoryName: string | null
  categoryColor: string | null
  isProjected: boolean
  attachmentCount: number
  transaction: Transaction | null
}

export function TransactionDrillDown({
  filter,
  onClose,
  onTransactionClick,
}: {
  filter: DrillDownFilter | null
  onClose: () => void
  onTransactionClick?: (tx: Transaction) => void
}) {
  const { t } = useTranslation()
  const { user } = useAuth()
  const { mask } = usePrivacyMode()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const panelRef = useRef<HTMLDivElement>(null)

  // The page belongs to the filter it was picked under: a new filter starts on
  // page 1 without an effect. The parent hands over a fresh filter object on
  // every render, so identity is no signal; the serialised filter is.
  const filterKey = JSON.stringify(filter)
  const [pageState, setPageState] = useState({ key: filterKey, page: 1 })
  const page = pageState.key === filterKey ? pageState.page : 1

  const { data, isLoading, isPlaceholderData: isChangingPage } = useQuery({
    queryKey: ['drill-down', filter, page],
    queryFn: () =>
      transactionsApi.list({
        category_id: filter?.category_id,
        uncategorized: filter?.uncategorized,
        account_id: filter?.account_id,
        account_ids: filter?.account_ids,
        type: filter?.type,
        from: filter?.from,
        to: filter?.to,
        // Posted rows only, like the Dashboard cards the panel opens from.
        status: 'posted',
        page,
        limit: DRILL_DOWN_PAGE_SIZE,
        user_pnl_only: true,
      }),
    enabled: !!filter,
    // Keep the previous page up while the next one loads, but never another
    // filter's rows under this filter's title.
    placeholderData: (previous, previousQuery) =>
      JSON.stringify(previousQuery?.queryKey[1]) === filterKey ? previous : undefined,
  })

  // Derive month param from filter.from for projected transactions
  const monthParam = filter?.from ? filter.from.slice(0, 7) + '-01' : undefined

  const { data: projectedTxs } = useQuery({
    queryKey: ['dashboard', 'projected-transactions', monthParam],
    queryFn: () => dashboard.projectedTransactions({ month: monthParam }),
    enabled: !!filter && !!monthParam,
  })

  const { data: accountingModeData } = useQuery({
    queryKey: ['admin', 'accounting-mode'],
    queryFn: () => admin.accountingMode(),
    staleTime: 5 * 60 * 1000,
  })
  const isAccrual = accountingModeData?.mode === 'accrual'

  const items = useMemo(
    (): DisplayItem[] =>
      (data?.items ?? []).map((tx) => ({
        key: tx.id,
        description: tx.description,
        date: tx.date,
        type: tx.type as 'debit' | 'credit',
        amount: Number(tx.amount),
        amountPrimary: tx.amount_primary != null ? Number(tx.amount_primary) : null,
        currency: tx.currency,
        categoryIcon: tx.category?.icon ?? null,
        categoryName: tx.category?.name ?? null,
        categoryColor: tx.category?.color ?? null,
        isProjected: false,
        attachmentCount: tx.attachment_count ?? 0,
        transaction: tx,
      })),
    [data],
  )

  // Planned transactions that match the filter. Listed apart and kept out of
  // the total, which is the server's sum of what has actually posted.
  const plannedItems = useMemo((): DisplayItem[] => {
    const planned: DisplayItem[] = []
    for (const pt of projectedTxs ?? []) {
      if (filter?.type && pt.type !== filter.type) continue
      if (filter?.category_id && String(pt.category_id) !== filter.category_id) continue
      if (filter?.uncategorized && pt.category_id != null) continue
      if (filter?.from && pt.date < filter.from) continue
      if (filter?.to && pt.date > filter.to) continue

      planned.push({
        key: `proj-${pt.recurring_id}-${pt.date}`,
        description: pt.description,
        date: pt.date,
        type: pt.type,
        amount: pt.amount,
        amountPrimary: pt.amount_primary ?? null,
        currency: pt.currency,
        categoryIcon: pt.category_icon,
        categoryName: pt.category_name,
        categoryColor: pt.category_color ?? null,
        isProjected: true,
        attachmentCount: 0,
        transaction: null,
      })
    }
    return planned.sort((a, b) => a.date.localeCompare(b.date))
  }, [projectedTxs, filter])

  // Close on Escape, unless the key belongs to a dialog opened from the
  // panel (Radix marks the Escape it handled as defaultPrevented).
  useEffect(() => {
    if (!filter) return
    const handleKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || hasOpenDialog()) return
      onClose()
    }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [filter, onClose])

  // Close on click outside. The transaction dialog opened from a row, and its
  // popovers and selects, are portaled to the body; clicks there keep the
  // panel open so the next row can be edited once the dialog closes.
  useEffect(() => {
    if (!filter) return
    const handleClick = (e: MouseEvent) => {
      if (!panelRef.current || panelRef.current.contains(e.target as Node)) return
      if (isInsideOverlayLayer(e.target) || hasOpenDialog()) return
      onClose()
    }
    // Delay to avoid closing immediately from the click that opened it
    const timer = setTimeout(() => {
      document.addEventListener('mousedown', handleClick)
    }, 100)
    return () => {
      clearTimeout(timer)
      document.removeEventListener('mousedown', handleClick)
    }
  }, [filter, onClose])

  const total = data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / DRILL_DOWN_PAGE_SIZE))
  const serverTotal = data?.summary
    ? drillDownServerTotal(data.summary, { type: filter?.type, uncategorized: filter?.uncategorized })
    : null
  const totalCurrency = data?.summary?.currency ?? userCurrency
  const isEmpty = items.length === 0 && plannedItems.length === 0

  const renderRow = (item: DisplayItem) => (
    <div
      key={item.key}
      className={`flex items-center gap-3 px-5 py-3 hover:bg-muted transition-colors ${!item.isProjected ? 'cursor-pointer' : ''}`}
      onClick={() => {
        if (!item.isProjected && item.transaction) {
          onTransactionClick?.(item.transaction)
        }
      }}
    >
      <CategoryIcon icon={item.categoryIcon} color={item.categoryColor} size="lg" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium text-foreground truncate">{item.description}</p>
          {item.isProjected && <ProjectedTransactionBadge />}
          {item.transaction && (
            <CategorizationOriginBadge
              source={item.transaction.category_source}
              pending={!!item.transaction.categorization_pending}
              compact
            />
          )}
          {item.attachmentCount > 0 && (
            <Paperclip size={12} className="text-muted-foreground shrink-0" />
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          {new Date(item.date + 'T00:00:00').toLocaleDateString(dateLocale)}
          {item.categoryName && ` · ${item.categoryName}`}
        </p>
      </div>
      <div className="text-right shrink-0">
        <span
          className={`text-sm font-semibold tabular-nums ${
            item.type === 'credit' ? 'text-emerald-600' : 'text-rose-500'
          }`}
        >
          {item.type === 'credit' ? '+' : '-'}
          {mask(formatCurrency(Math.abs(item.amount), item.currency ?? userCurrency, locale))}
        </span>
        {item.currency !== userCurrency && item.amountPrimary != null && (
          <div className="flex items-center justify-end gap-1">
            {item.transaction?.fx_fallback && (
              <span title={t('transactions.fxFallbackTooltip')}><AlertTriangle size={11} className="text-amber-500 shrink-0" /></span>
            )}
            <span className="text-[10px] text-muted-foreground tabular-nums">
              {mask(formatCurrency(Math.abs(item.amountPrimary), userCurrency, locale))}
            </span>
          </div>
        )}
      </div>
    </div>
  )

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 bg-black/20 z-40 transition-opacity duration-200 ${
          filter ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
      />

      {/* Panel */}
      <div
        ref={panelRef}
        className={`fixed top-0 right-0 h-full w-full max-w-md bg-card shadow-2xl z-50 transform transition-transform duration-200 ease-out flex flex-col ${
          filter ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
          <h2 className="text-sm font-semibold text-foreground truncate pr-4">
            {filter?.title}
          </h2>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors shrink-0"
            title={t('common.close')}
          >
            <X size={16} />
          </button>
        </div>

        {isAccrual && filter?.from && (
          <div className="flex items-start gap-2 px-5 py-2.5 bg-muted/40 border-b border-border text-[11px] text-muted-foreground shrink-0">
            <Info size={12} className="mt-0.5 shrink-0" />
            <span>{t('dashboard.accrualNote')}</span>
          </div>
        )}

        {/* Transaction list */}
        <div className="flex-1 overflow-auto" aria-busy={isChangingPage}>
          {isLoading ? (
            <div className="p-5 space-y-3">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="h-12 bg-muted rounded-lg animate-pulse" />
              ))}
            </div>
          ) : isEmpty ? (
            <p className="text-muted-foreground text-sm text-center py-12">
              {t('dashboard.drillDownEmpty')}
            </p>
          ) : (
            <>
              {items.length > 0 && (
                <div className={`divide-y divide-border transition-opacity ${isChangingPage ? 'opacity-60' : ''}`}>
                  {items.map(renderRow)}
                </div>
              )}
              {plannedItems.length > 0 && (
                <section aria-label={t('dashboard.drillDownPlanned')}>
                  <h3 className="px-5 py-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground bg-muted/40 border-y border-border">
                    {t('dashboard.drillDownPlanned')}
                  </h3>
                  <div className="divide-y divide-border">{plannedItems.map(renderRow)}</div>
                </section>
              )}
            </>
          )}
        </div>

        {/* Footer: the server's total for the whole filter, then the pager */}
        {total > 0 && serverTotal !== null && (
          <div className="px-5 py-3 border-t border-border bg-muted/50 shrink-0 space-y-2">
            <div className="flex items-center justify-between gap-4">
              <span className="text-xs text-muted-foreground">
                {t('dashboard.drillDownCount', { count: total })}
              </span>
              <span className="text-sm font-bold tabular-nums text-foreground" data-testid="drill-down-total">
                {mask(formatCurrency(serverTotal, totalCurrency, locale))}
              </span>
            </div>
            {pages > 1 && (
              <div className="flex items-center justify-between">
                <button
                  type="button"
                  aria-label={t('reports.explore.prevPage')}
                  disabled={page <= 1}
                  onClick={() => setPageState({ key: filterKey, page: page - 1 })}
                  className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:pointer-events-none"
                >
                  <ChevronLeft size={16} />
                </button>
                <span className="text-xs text-muted-foreground tabular-nums">
                  {t('reports.explore.pageOf', { page, pages })}
                </span>
                <button
                  type="button"
                  aria-label={t('reports.explore.nextPage')}
                  disabled={page >= pages}
                  onClick={() => setPageState({ key: filterKey, page: page + 1 })}
                  className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:pointer-events-none"
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </>
  )
}
