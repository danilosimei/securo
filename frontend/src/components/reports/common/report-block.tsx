/** The shell of every Reports block: a card with a title, a one-line answer
 *  under it, and the loading / empty / error states, so each chart only
 *  draws its data. A refetch keeps the previous render, dimmed. */
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { SectionCard } from '@/components/invoice-ui'
import type { BlockState } from './block-state'

interface ReportBlockProps {
  title: string
  /** One line under the title: what the chart answers, or how to read it. */
  description?: ReactNode
  action?: ReactNode
  state: BlockState
  stale?: boolean
  onRetry?: () => void
  emptyText?: string
  /** Skeleton height while loading. */
  skeletonHeight?: number
  className?: string
  bodyClassName?: string
  testId?: string
  children?: ReactNode
}

export function ReportBlock({
  title,
  description,
  action,
  state,
  stale,
  onRetry,
  emptyText,
  skeletonHeight = 220,
  className,
  bodyClassName,
  testId,
  children,
}: ReportBlockProps) {
  const { t } = useTranslation()
  return (
    <SectionCard className={cn('overflow-visible', className)}>
      <section aria-label={title} data-testid={testId}>
        <div className="flex flex-wrap items-start justify-between gap-2 px-4 pt-4 sm:px-5">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-foreground">{title}</h2>
            {description && state === 'ready' && (
              <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
            )}
          </div>
          {state === 'ready' && action}
        </div>
        <div className={cn('px-4 pb-4 pt-3 sm:px-5', bodyClassName)}>
          {state === 'loading' && (
            <div aria-busy="true">
              <Skeleton className="w-full" style={{ height: skeletonHeight }} />
            </div>
          )}
          {state === 'error' && <BlockError onRetry={onRetry} />}
          {state === 'empty' && (
            <p className="py-8 text-center text-sm text-muted-foreground">{emptyText ?? t('reports.common.empty')}</p>
          )}
          {state === 'ready' && (
            <div aria-busy={stale || undefined} className={cn('transition-opacity duration-200', stale && 'opacity-60')}>
              {children}
            </div>
          )}
        </div>
      </section>
    </SectionCard>
  )
}

export function BlockError({ onRetry, message }: { onRetry?: () => void; message?: string }) {
  const { t } = useTranslation()
  return (
    <div
      role="alert"
      className="flex items-center justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-2.5 dark:border-rose-500/30 dark:bg-rose-500/10"
    >
      <div className="flex min-w-0 items-center gap-2.5">
        <AlertCircle size={16} className="shrink-0 text-rose-600 dark:text-rose-400" aria-hidden="true" />
        <span className="text-sm text-rose-900 dark:text-rose-200">{message ?? t('reports.common.error')}</span>
      </div>
      {onRetry && (
        <Button type="button" variant="ghost" size="sm" onClick={onRetry}>
          {t('reports.common.retry')}
        </Button>
      )}
    </div>
  )
}

/** The sentence a tab opens with: the answer before any chart. */
export function Headline({ text, loading, stale }: { text: string | null; loading?: boolean; stale?: boolean }) {
  if (loading) {
    return (
      <div aria-busy="true" className="space-y-2">
        <Skeleton className="h-6 w-full max-w-xl" />
        <Skeleton className="h-6 w-2/3 max-w-md" />
      </div>
    )
  }
  if (!text) return null
  return (
    <p
      data-testid="report-headline"
      aria-live="polite"
      className={cn(
        'max-w-3xl text-lg font-semibold leading-snug text-foreground text-balance transition-opacity duration-200 sm:text-xl',
        stale && 'opacity-60',
      )}
    >
      {text}
    </p>
  )
}

/** Floating readout for a hovered or focused mark. Values lead, labels follow. */
export function ChartTooltip({
  x,
  y,
  width,
  children,
}: {
  x: number
  y: number
  /** Width of the chart, to keep the box inside it. */
  width: number
  children: ReactNode
}) {
  const flip = x > width - 180
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute z-20 min-w-36 max-w-60 rounded-lg border border-border bg-card px-3 py-2 text-xs shadow-lg"
      style={{
        left: flip ? undefined : x + 12,
        right: flip ? Math.max(0, width - x + 12) : undefined,
        top: Math.max(0, y - 8),
      }}
    >
      {children}
    </div>
  )
}

/** One line of a tooltip: a short key in the series color, value first. */
export function TooltipRow({ color, label, value, strong }: { color?: string; label: string; value: string; strong?: boolean }) {
  return (
    <div className="flex items-center gap-2 py-0.5">
      {color && <span className="h-0.5 w-3 shrink-0 rounded-full" style={{ background: color }} />}
      <span className={cn('tabular-nums text-foreground', strong ? 'font-semibold' : 'font-medium')}>{value}</span>
      <span className="min-w-0 truncate text-muted-foreground">{label}</span>
    </div>
  )
}
