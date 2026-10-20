/** The organizer: Claude proposes a better category tree as a list of
 *  operations, and the person approves or rejects each one.
 *
 *  Every structural change has to be understood before it is approved, so
 *  the predicted tree sits next to the operations (new items green, renamed
 *  and moved ones with what they were, merged ones struck through) and each
 *  card says in plain words what happens to the transactions. Tree changes
 *  have no undo; transaction moves can be undone in "Applied". */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  AlertCircle,
  Check,
  Info,
  Loader2,
  RefreshCw,
  Sparkles,
  WandSparkles,
  X,
} from 'lucide-react'
import {
  categories as categoriesApi,
  categoryGroups as categoryGroupsApi,
  categorization as categorizationApi,
} from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateCategorizationQueries, invalidateCategoryQueries } from '@/lib/invalidate-queries'
import { organizerPollInterval, redistributionBusy } from '@/lib/categorization-organizer'
import { formatCurrency } from '@/lib/format'
import { formatRelativeFromNow } from '@/lib/relative-time'
import { cn } from '@/lib/utils'
import { useWorkspace } from '@/contexts/workspace-context'
import { useAuth } from '@/contexts/auth-context'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { SectionCard, SectionHeader } from '@/components/invoice-ui'
import { PredictedTree } from '@/components/categorization/organizer-tree'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { CategorizationRun, OrganizerOp, OrganizerView } from '@/types'

export const ORGANIZER_QUERY_KEY = ['categorization', 'organizer'] as const
const RUN_BUSY = new Set(['queued', 'running'])
const SHORT_GROUP = /^g\d+$/

/** 409 (the tree changed, a name is taken) and 404 (the op is gone) are
 *  answered with a new proposal, not with a retry. */
function isConflict(error: unknown): boolean {
  const status = (error as { response?: { status?: number } } | null)?.response?.status
  return status === 409 || status === 404
}

function opTitle(op: OrganizerOp, t: TFunction): string {
  const labels = op.labels ?? {}
  const target = labels.target ?? op.target ?? ''
  switch (op.op) {
    case 'create_group':
      return t('categorization.organize.title.create_group', { name: op.name ?? '' })
    case 'create_category':
      return t('categorization.organize.title.create_category', {
        path: `${labels.group ?? op.group ?? ''} › ${op.name ?? ''}`,
      })
    case 'rename':
      return t(
        op.target && SHORT_GROUP.test(op.target)
          ? 'categorization.organize.title.renameGroup'
          : 'categorization.organize.title.rename',
        { from: target, to: op.name ?? '' },
      )
    case 'move_category':
      return t('categorization.organize.title.move_category', {
        name: target,
        group: labels.group ?? op.group ?? '',
      })
    case 'merge':
      return t('categorization.organize.title.merge', { from: target, into: labels.into ?? op.into ?? '' })
    case 'split_category':
      return t('categorization.organize.title.split_category', { name: target, count: op.children.length })
    default:
      return t('categorization.organize.title.unknown')
  }
}

type Confirm =
  | { kind: 'one'; op: OrganizerOp }
  | { kind: 'all'; merges: OrganizerOp[] }
  | { kind: 'regenerate'; pending: number }

const norm = (value: string) => value.normalize('NFC').trim().toLocaleLowerCase()

/** A proposed group that later pending ops of the proposal put things in. */
function groupUsedLater(op: OrganizerOp, ops: OrganizerOp[]): boolean {
  if (op.op !== 'create_group' || !op.name) return false
  const name = norm(op.name)
  return ops
    .slice(ops.indexOf(op) + 1)
    .filter((o) => o.status === 'pending')
    .some((o) => {
      const ref =
        o.op === 'split_category' ? o.name : o.op === 'create_category' || o.op === 'move_category' ? o.group : null
      return !!ref && norm(ref) === name
    })
}

export function OrganizeTab() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { canManage } = useWorkspace()
  const locale = useDateLocale()

  const organizer = useQuery({
    queryKey: [...ORGANIZER_QUERY_KEY],
    queryFn: categorizationApi.organizer,
    refetchInterval: (query) => organizerPollInterval(query.state.data),
  })
  // Same keys as the review tab and the transactions page: a shared cache.
  const categoriesQuery = useQuery({ queryKey: ['categories'], queryFn: categoriesApi.list })
  const groupsQuery = useQuery({ queryKey: ['categoryGroups'], queryFn: categoryGroupsApi.list })

  const view = organizer.data
  const run = view?.run ?? null
  const ops = useMemo(() => view?.ops ?? [], [view])
  const runId = run?.id

  const [errors, setErrors] = useState<Record<string, string>>({})
  const [busyOp, setBusyOp] = useState<string | null>(null)
  const [applyingAll, setApplyingAll] = useState(false)
  const [starting, setStarting] = useState(false)
  const [confirm, setConfirm] = useState<Confirm | null>(null)
  const [highlight, setHighlight] = useState<string | null>(null)
  // Conflict messages belong to the proposal they were raised on.
  const [errorsRun, setErrorsRun] = useState(runId)
  if (errorsRun !== runId) {
    setErrorsRun(runId)
    setErrors({})
  }
  // Ops applied here whose effect the category lists have not caught up
  // with yet: the predicted tree keeps simulating them until they do.
  const [appliedAt, setAppliedAt] = useState<Record<string, number>>({})
  const treeSyncedAt = Math.min(categoriesQuery.dataUpdatedAt, groupsQuery.dataUpdatedAt)
  const treeOps = useMemo(
    () =>
      ops.map((op) =>
        op.status === 'applied' && (appliedAt[op.id] ?? 0) > treeSyncedAt ? { ...op, status: 'pending' as const } : op,
      ),
    [ops, appliedAt, treeSyncedAt],
  )

  const replaceOp = (next: OrganizerOp, redistribution?: CategorizationRun | null) => {
    queryClient.setQueryData<OrganizerView>([...ORGANIZER_QUERY_KEY], (old) =>
      old
        ? {
            ...old,
            ops: old.ops.map((o) =>
              o.id === next.id
                ? { ...o, ...next, redistribution: redistribution ?? next.redistribution ?? o.redistribution ?? null }
                : o,
            ),
          }
        : old,
    )
  }
  const setError = (opId: string, message: string | null) =>
    setErrors((prev) => {
      const next = { ...prev }
      if (message === null) delete next[opId]
      else next[opId] = message
      return next
    })
  const refreshAfterApply = () => {
    invalidateCategoryQueries(queryClient)
    invalidateCategorizationQueries(queryClient)
  }

  // A split's redistribution finishing moved transactions: refresh what shows them.
  const busyRedistributions = useRef(new Set<string>())
  useEffect(() => {
    const now = new Set(ops.filter((op) => redistributionBusy(op.redistribution)).map((op) => op.redistribution!.id))
    const finished = [...busyRedistributions.current].some((id) => !now.has(id))
    busyRedistributions.current = now
    if (finished) invalidateCategorizationQueries(queryClient)
  }, [ops, queryClient])

  async function startRun() {
    setStarting(true)
    try {
      const started = await categorizationApi.runOrganizer()
      setErrors({})
      queryClient.setQueryData<OrganizerView>([...ORGANIZER_QUERY_KEY], { run: started, ops: [], message: null })
      queryClient.invalidateQueries({ queryKey: [...ORGANIZER_QUERY_KEY] })
    } catch (error) {
      toast.error(extractApiError(error, t('categorization.organize.runError')))
    } finally {
      setStarting(false)
    }
  }

  /** One op; null when applied, else the reason it was not. */
  async function applyOne(op: OrganizerOp): Promise<string | null> {
    setBusyOp(op.id)
    try {
      const result = await categorizationApi.applyOrganizerOp(op.id, runId)
      setAppliedAt((prev) => ({ ...prev, [op.id]: Date.now() }))
      replaceOp(result.op, result.redistribute_run)
      setError(op.id, null)
      // The tree changed: refetch it now, not only at the end of a batch.
      invalidateCategoryQueries(queryClient)
      return null
    } catch (error) {
      const message = extractApiError(error, t('categorization.organize.applyError'))
      if (isConflict(error)) setError(op.id, message)
      return message
    } finally {
      setBusyOp(null)
    }
  }

  async function apply(op: OrganizerOp) {
    const failure = await applyOne(op)
    refreshAfterApply()
    if (failure === null) toast.success(t('categorization.organize.applied'))
    else toast.error(failure)
  }

  async function applyAll() {
    const queue = ops.filter((op) => op.status === 'pending')
    setApplyingAll(true)
    let done = 0
    try {
      for (const op of queue) {
        const failure = await applyOne(op)
        if (failure !== null) {
          toast.error(
            t('categorization.organize.applyAllStopped', {
              title: opTitle(op, t),
              done,
              total: queue.length,
              error: failure,
            }),
          )
          return
        }
        done += 1
      }
      toast.success(t('categorization.organize.allApplied', { count: done }))
    } finally {
      setApplyingAll(false)
      // Even a batch stopped at its first op: the tree may have moved under it.
      refreshAfterApply()
    }
  }

  async function reject(op: OrganizerOp) {
    setBusyOp(op.id)
    try {
      const result = await categorizationApi.rejectOrganizerOp(op.id, runId)
      replaceOp(result)
      setError(op.id, null)
      queryClient.invalidateQueries({ queryKey: [...ORGANIZER_QUERY_KEY] })
    } catch (error) {
      const message = extractApiError(error, t('categorization.organize.rejectError'))
      if (isConflict(error)) setError(op.id, message)
      toast.error(message)
    } finally {
      setBusyOp(null)
    }
  }

  async function retry(op: OrganizerOp) {
    setBusyOp(op.id)
    try {
      const result = await categorizationApi.retryOrganizerOp(op.id, runId)
      replaceOp(result.op, result.redistribute_run)
      queryClient.invalidateQueries({ queryKey: [...ORGANIZER_QUERY_KEY] })
    } catch (error) {
      toast.error(extractApiError(error, t('categorization.organize.retryError')))
    } finally {
      setBusyOp(null)
    }
  }

  const pending = ops.filter((op) => op.status === 'pending')
  const locked = applyingAll || busyOp !== null || starting

  const requestApply = (op: OrganizerOp) => {
    if (op.op === 'merge') setConfirm({ kind: 'one', op })
    else void apply(op)
  }
  const requestApplyAll = () => {
    const merges = pending.filter((op) => op.op === 'merge')
    if (merges.length > 0) setConfirm({ kind: 'all', merges })
    else void applyAll()
  }
  /** A new proposal costs money and replaces this one: confirm when it
   *  still has decisions to make. */
  const requestNewProposal = () => {
    if (pending.length > 0) setConfirm({ kind: 'regenerate', pending: pending.length })
    else void startRun()
  }
  const confirmDialog = () => {
    const current = confirm
    setConfirm(null)
    if (!current) return
    if (current.kind === 'one') void apply(current.op)
    else if (current.kind === 'all') void applyAll()
    else void startRun()
  }

  let body: React.ReactNode
  if (organizer.isLoading) {
    body = (
      <SectionCard className="p-5">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="mt-3 h-3 w-80 max-w-full" />
        <Skeleton className="mt-6 h-24 w-full" />
      </SectionCard>
    )
  } else if (!view) {
    body = (
      <StateCard icon={<AlertCircle size={20} className="text-rose-500" />} title={t('categorization.organize.loadError')}>
        <Button variant="outline" onClick={() => void organizer.refetch()} disabled={organizer.isFetching} className="gap-2">
          {organizer.isFetching ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
          {t('categorization.organize.retry')}
        </Button>
      </StateCard>
    )
  } else if (!run) {
    body = (
      <StateCard
        icon={<WandSparkles size={20} className="text-primary" />}
        title={t('categorization.organize.emptyTitle')}
        hint={t('categorization.organize.emptyHint')}
      >
        {canManage ? (
          <>
            <Button onClick={startRun} disabled={starting} className="gap-2">
              {starting ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
              {t('categorization.organize.analyze')}
            </Button>
            <p className="text-xs text-muted-foreground">{t('categorization.organize.costNote')}</p>
          </>
        ) : (
          <p className="text-xs text-muted-foreground">{t('categorization.organize.managersOnly')}</p>
        )}
      </StateCard>
    )
  } else if (RUN_BUSY.has(run.status)) {
    body = (
      <StateCard
        icon={<Loader2 size={20} className="animate-spin text-primary" />}
        title={t('categorization.organize.running')}
        hint={t('categorization.organize.runningHint')}
      />
    )
  } else if (run.status !== 'done') {
    body = (
      <StateCard
        icon={<AlertCircle size={20} className="text-amber-500" />}
        title={view.message ?? t('categorization.organize.failed')}
      >
        {canManage && (
          <div className="flex flex-wrap items-center justify-center gap-2">
            <Button onClick={startRun} disabled={starting} className="gap-2">
              {starting ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
              {t('categorization.organize.retry')}
            </Button>
            {run.error === 'no_ai_connection' && (
              <Button variant="outline" asChild>
                <Link to="/workspace/settings#categorization">{t('categorization.organize.configure')}</Link>
              </Button>
            )}
          </div>
        )}
      </StateCard>
    )
  } else if (ops.length === 0) {
    body = (
      <StateCard icon={<Check size={20} className="text-emerald-500" />} title={t('categorization.organize.noChanges')}>
        {canManage && (
          <>
            <Button variant="outline" onClick={startRun} disabled={starting} className="gap-2">
              {starting ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
              {t('categorization.organize.analyzeAgain')}
            </Button>
            <p className="text-xs text-muted-foreground">{t('categorization.organize.costNote')}</p>
          </>
        )}
      </StateCard>
    )
  } else {
    body = (
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <PredictedTree
          className="order-2 lg:order-1"
          groups={groupsQuery.data ?? []}
          categories={categoriesQuery.data ?? []}
          ops={treeOps}
          highlight={highlight}
        />
        <SectionCard className="order-1 lg:order-2">
          <SectionHeader
            title={t('categorization.organize.opsTitle')}
            description={[
              run.created_at
                ? t('categorization.organize.proposedAt', { when: formatRelativeFromNow(run.created_at, locale) })
                : null,
              t('categorization.organize.pendingCount', { count: pending.length }),
            ]
              .filter(Boolean)
              .join(' · ')}
            action={
              canManage ? (
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 gap-1.5"
                    onClick={requestNewProposal}
                    disabled={locked}
                    title={t('categorization.organize.costNote')}
                  >
                    <RefreshCw size={13} className={starting ? 'animate-spin' : undefined} />
                    {t('categorization.organize.newProposal')}
                  </Button>
                  {pending.length > 0 && (
                    <Button size="sm" className="h-8 gap-1.5" onClick={requestApplyAll} disabled={locked}>
                      {applyingAll ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                      {t('categorization.organize.applyAll', { count: pending.length })}
                    </Button>
                  )}
                </div>
              ) : undefined
            }
          />
          <div className="flex items-start gap-2 border-b border-border bg-muted/40 px-4 py-2 text-xs text-muted-foreground sm:px-5">
            <Info size={13} className="mt-px shrink-0" />
            <div className="space-y-0.5">
              <p>{t('categorization.organize.opsHint')}</p>
              {!canManage && <p>{t('categorization.organize.managersOnly')}</p>}
            </div>
          </div>
          <ul className="space-y-2 p-3 sm:p-4">
            {ops.map((op) => (
              <OpCard
                key={op.id}
                op={op}
                canManage={canManage}
                busy={busyOp === op.id}
                locked={locked}
                error={errors[op.id] ?? null}
                onApply={() => requestApply(op)}
                onReject={() => reject(op)}
                onRetry={() => retry(op)}
                onRegenerate={requestNewProposal}
                onHighlight={setHighlight}
                groupUsedLater={groupUsedLater(op, ops)}
              />
            ))}
          </ul>
        </SectionCard>
      </div>
    )
  }

  return (
    <div data-testid="categorization-organize">
      {organizer.isError && view && (
        <div
          role="status"
          className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-amber-300/50 bg-amber-50/40 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950/15 dark:text-amber-200"
        >
          <AlertCircle size={13} className="shrink-0" />
          <span className="min-w-0 flex-1">{t('categorization.organize.refreshError')}</span>
          <button
            type="button"
            className="font-medium underline-offset-2 hover:underline disabled:opacity-50"
            onClick={() => void organizer.refetch()}
            disabled={organizer.isFetching}
          >
            {t('categorization.organize.retry')}
          </button>
        </div>
      )}
      {body}
      <Dialog open={confirm !== null} onOpenChange={(open) => !open && setConfirm(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {confirm?.kind === 'regenerate'
                ? t('categorization.organize.newProposalTitle')
                : t('categorization.organize.mergeTitle')}
            </DialogTitle>
            <DialogDescription>
              {confirm?.kind === 'one'
                ? t('categorization.organize.mergeDescription', {
                    from: confirm.op.labels?.target ?? confirm.op.target ?? '',
                    into: confirm.op.labels?.into ?? confirm.op.into ?? '',
                    transactions: t('categorization.organize.transactions', { count: confirm.op.impact?.tx ?? 0 }),
                  })
                : confirm?.kind === 'regenerate'
                  ? t('categorization.organize.newProposalDescription', { count: confirm.pending })
                  : t('categorization.organize.mergeAllDescription', {
                      count: confirm?.kind === 'all' ? confirm.merges.length : 0,
                    })}
            </DialogDescription>
          </DialogHeader>
          {confirm?.kind === 'all' && (
            <div className="space-y-1">
              {confirm.merges.map((op) => (
                <div key={op.id} className="rounded bg-muted px-2 py-1 text-sm">
                  {op.labels?.target ?? op.target} → {op.labels?.into ?? op.into}
                </div>
              ))}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(null)}>
              {t('common.cancel')}
            </Button>
            <Button variant={confirm?.kind === 'regenerate' ? 'default' : 'destructive'} onClick={confirmDialog}>
              {confirm?.kind === 'all'
                ? t('categorization.organize.applyAllConfirm')
                : confirm?.kind === 'regenerate'
                  ? t('categorization.organize.newProposal')
                  : t('categorization.organize.mergeConfirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function StateCard({
  icon,
  title,
  hint,
  children,
}: {
  icon: React.ReactNode
  title: string
  hint?: string
  children?: React.ReactNode
}) {
  return (
    <SectionCard>
      <div className="mx-auto flex max-w-lg flex-col items-center gap-3 px-5 py-10 text-center">
        {icon}
        <div>
          <p className="text-sm font-medium text-foreground">{title}</p>
          {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
        </div>
        {children}
      </div>
    </SectionCard>
  )
}

// --- the operations -----------------------------------------------------------

const STATUS_STYLE = {
  pending: 'border-amber-300/50 bg-amber-50/40 dark:bg-amber-950/15',
  applied: 'border-emerald-300/40 bg-emerald-50/30 dark:bg-emerald-950/15',
  rejected: 'border-border bg-muted/40 opacity-70',
} as const
const BADGE_STYLE = {
  pending: 'bg-amber-200/70 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200',
  applied: 'bg-emerald-200/70 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-200',
  rejected: 'bg-muted text-muted-foreground',
} as const

function OpCard({
  op,
  canManage,
  busy,
  locked,
  error,
  onApply,
  onReject,
  onRetry,
  onRegenerate,
  onHighlight,
  groupUsedLater = false,
}: {
  op: OrganizerOp
  canManage: boolean
  busy: boolean
  locked: boolean
  error: string | null
  onApply: () => void
  onReject: () => void
  onRetry: () => void
  onRegenerate: () => void
  onHighlight: (opId: string | null) => void
  groupUsedLater?: boolean
}) {
  const { t } = useTranslation()
  const { user } = useAuth()
  const currency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const status = op.status in STATUS_STYLE ? op.status : 'pending'
  const tx = op.impact?.tx ?? 0

  return (
    <li
      data-testid={`organizer-op-${op.id}`}
      data-status={status}
      className={cn('overflow-hidden rounded-md border text-sm transition-colors', STATUS_STYLE[status])}
      onMouseEnter={() => onHighlight(op.id)}
      onMouseLeave={() => onHighlight(null)}
      onFocus={() => onHighlight(op.id)}
      onBlur={() => onHighlight(null)}
    >
      <div className="flex items-start gap-3 px-3 py-2.5">
        <div className="mt-0.5 shrink-0">
          {status === 'applied' ? (
            <Check className="h-4 w-4 text-emerald-600" />
          ) : status === 'rejected' ? (
            <X className="h-4 w-4 text-muted-foreground" />
          ) : (
            <Sparkles className="h-4 w-4 text-amber-600" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-foreground">{opTitle(op, t)}</span>
            <span className={cn('rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wider', BADGE_STYLE[status])}>
              {t(`categorization.organize.status.${status}`)}
            </span>
          </div>
          {op.why && <p className="mt-1 text-[13px] leading-snug text-muted-foreground">{op.why}</p>}
          {tx > 0 && (
            <p className="mt-1 text-xs tabular-nums text-muted-foreground">
              {t('categorization.organize.impact', {
                count: tx,
                total: mask(formatCurrency(Number(op.impact.total) || 0, currency, locale)),
              })}
            </p>
          )}
          <OpEffect op={op} />
          {groupUsedLater && (
            <p className="mt-1 text-xs text-muted-foreground">{t('categorization.organize.effect.groupUsedLater')}</p>
          )}
          {op.op === 'split_category' && status === 'applied' && (
            <Redistribution op={op} canManage={canManage} busy={busy} locked={locked} onRetry={onRetry} />
          )}
          {error && (
            <div className="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-rose-300/50 bg-rose-50/50 px-2 py-1.5 text-[13px] text-rose-700 dark:bg-rose-950/20 dark:text-rose-200">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span className="min-w-0 flex-1">{error}</span>
              {canManage && (
                <Button size="sm" variant="outline" className="h-7" onClick={onRegenerate} disabled={locked}>
                  {t('categorization.organize.newProposal')}
                </Button>
              )}
            </div>
          )}
          {canManage && status === 'pending' && (
            <div className="mt-2.5 flex gap-2">
              <Button size="sm" className="h-8 gap-1.5" onClick={onApply} disabled={locked}>
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                {t('categorization.organize.apply')}
              </Button>
              <Button size="sm" variant="ghost" className="h-8" onClick={onReject} disabled={locked}>
                {t('categorization.organize.reject')}
              </Button>
            </div>
          )}
        </div>
      </div>
    </li>
  )
}

/** What happens to the transactions, in plain words. */
function OpEffect({ op }: { op: OrganizerOp }) {
  const { t } = useTranslation()
  const labels = op.labels ?? {}
  const target = labels.target ?? op.target ?? ''
  const tx = op.impact?.tx ?? 0
  const transactions = t('categorization.organize.transactions', { count: tx })
  const line = (text: string) => <p className="mt-1.5 text-[13px] leading-snug text-foreground/90">{text}</p>

  switch (op.op) {
    case 'create_group':
      return line(t('categorization.organize.effect.createGroup'))
    case 'create_category': {
      const stores = labels.children ?? []
      if (tx === 0) return line(t('categorization.organize.effect.createCategoryEmpty'))
      return (
        <>
          {line(
            t('categorization.organize.effect.createCategory', {
              transactions,
              stores: t('categorization.organize.stores', { count: stores.length || op.children.length }),
              path: `${labels.group ?? op.group ?? ''} › ${op.name ?? ''}`,
            }),
          )}
          {stores.length > 0 && (
            <p className="mt-1 text-xs text-muted-foreground">
              {t('categorization.organize.effect.storeList', { names: stores.join(', ') })}
            </p>
          )}
        </>
      )
    }
    case 'rename':
      return line(t('categorization.organize.effect.rename'))
    case 'move_category':
      return line(t('categorization.organize.effect.move', { name: target }))
    case 'merge':
      return line(
        t('categorization.organize.effect.merge', { from: target, into: labels.into ?? op.into ?? '', transactions }),
      )
    case 'split_category': {
      const [first] = op.children
      return (
        <>
          {line(t('categorization.organize.effect.split', { transactions, name: target }))}
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
            {op.name && (
              <span className="text-muted-foreground">
                {t('categorization.organize.effect.splitGroup', { group: op.name })}
              </span>
            )}
            {op.children.map((child) => (
              <span key={child} className="rounded-full border border-emerald-300/60 bg-emerald-50/60 px-2 py-0.5 text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-200">
                {child}
              </span>
            ))}
          </div>
          {first && (
            <p className="mt-1 text-xs text-muted-foreground">
              {t('categorization.organize.effect.splitHeir', { first, name: target })}
            </p>
          )}
        </>
      )
    }
    default:
      return null
  }
}

function Redistribution({
  op,
  canManage,
  busy,
  locked,
  onRetry,
}: {
  op: OrganizerOp
  canManage: boolean
  busy: boolean
  locked: boolean
  onRetry: () => void
}) {
  const { t } = useTranslation()
  const run = op.redistribution
  if (!run) return null
  const first = op.children[0] ?? op.labels?.target ?? ''
  const box = 'mt-2 flex flex-wrap items-center gap-2 text-[13px]'
  if (redistributionBusy(run)) {
    return (
      <p className={cn(box, 'text-muted-foreground')}>
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        {t('categorization.organize.redistributing')}
      </p>
    )
  }
  const stat = (key: string) => Number(run.stats?.[key] ?? 0) || 0
  if (run.status === 'done') {
    return (
      <p className={cn(box, 'text-emerald-700 dark:text-emerald-300')}>
        <Check className="h-3.5 w-3.5" />
        {t('categorization.organize.redistributed', {
          applied: stat('applied') + stat('memory'),
          kept: stat('skipped') + stat('confirmed'),
        })}
      </p>
    )
  }
  const retryButton = canManage && (
    <Button size="sm" variant="outline" className="h-7 gap-1.5" onClick={onRetry} disabled={locked}>
      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
      {t('categorization.organize.retry')}
    </Button>
  )
  if (run.status === 'capped') {
    // The rows left in the first child already hold a category, so nothing
    // asks about them in Review: only a retry (after raising the cap) moves them.
    return (
      <div className={cn(box, 'text-amber-700 dark:text-amber-300')}>
        <AlertCircle className="h-3.5 w-3.5" />
        <span>{t('categorization.organize.redistributeCapped', { name: first })}</span>
        {canManage && (
          <Button size="sm" variant="outline" className="h-7" asChild>
            <Link to="/workspace/settings#categorization">{t('categorization.organize.adjustCap')}</Link>
          </Button>
        )}
        {retryButton}
      </div>
    )
  }
  return (
    <div className={cn(box, 'text-rose-700 dark:text-rose-200')}>
      <AlertCircle className="h-3.5 w-3.5" />
      <span>{t('categorization.organize.redistributeFailed', { name: first })}</span>
      {canManage && run.error === 'no_ai_connection' ? (
        <Button size="sm" variant="outline" className="h-7" asChild>
          <Link to="/workspace/settings#categorization">{t('categorization.organize.configure')}</Link>
        </Button>
      ) : (
        retryButton
      )}
    </div>
  )
}
