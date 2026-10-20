import { useEffect, useRef } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Loader2, Settings, Sparkles } from 'lucide-react'
import { categorization as categorizationApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateCategorizationQueries } from '@/lib/invalidate-queries'
import { isCategorizationBusy, useCategorizationSummary } from '@/hooks/use-categorization-summary'
import { useWorkspace } from '@/contexts/workspace-context'
import { useDateLocale } from '@/hooks/use-display-locale'
import { PageHeader } from '@/components/page-header'
import { Segmented } from '@/components/invoice-ui'
import { Button } from '@/components/ui/button'
import { ReviewTab } from '@/components/categorization/review-tab'
import { AppliedTab } from '@/components/categorization/applied-tab'
import { OrganizeTab } from '@/components/categorization/organize-tab'

type Tab = 'review' | 'applied' | 'organize'
const TABS: Tab[] = ['review', 'applied', 'organize']
/**
 * Own helper rather than `lib/relative-time.formatRelative`: that one emits
 * hard-coded English ("2h ago"), and this line is localized copy.
 */
function relativeFromNow(iso: string, locale: string): string {
  const diffSec = Math.round((new Date(iso).getTime() - Date.now()) / 1000)
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
  const abs = Math.abs(diffSec)
  if (abs < 60) return rtf.format(0, 'second')
  if (abs < 3600) return rtf.format(Math.round(diffSec / 60), 'minute')
  if (abs < 86400) return rtf.format(Math.round(diffSec / 3600), 'hour')
  return rtf.format(Math.round(diffSec / 86400), 'day')
}

export default function CategorizationPage() {
  const { t } = useTranslation()
  const locale = useDateLocale()
  const queryClient = useQueryClient()
  const { canWrite, canManage } = useWorkspace()

  // Addressable like the rules page, so a deep link (or the dashboard card)
  // lands on the right tab. `replace` keeps Back from walking through tabs.
  const [searchParams, setSearchParams] = useSearchParams()
  const requested = searchParams.get('tab')
  const tab: Tab = TABS.find((x) => x === requested) ?? 'review'
  const setTab = (next: Tab) =>
    setSearchParams(next === 'review' ? {} : { tab: next }, { replace: true })

  // Shared with the sidebar badge; polls only while a run is in flight.
  const { data: summary } = useCategorizationSummary()

  const run = useMutation({
    mutationFn: () => categorizationApi.run({ scope: 'backlog' }),
    onSuccess: () => {
      toast.success(t('categorization.runStarted'))
      queryClient.invalidateQueries({ queryKey: ['categorization', 'summary'] })
    },
    onError: (error) => toast.error(extractApiError(error, t('categorization.runError'))),
  })

  const busy = run.isPending || isCategorizationBusy(summary)

  // A run finishing changes what the other tabs show; refresh them once.
  const wasBusy = useRef(false)
  useEffect(() => {
    if (wasBusy.current && !busy) invalidateCategorizationQueries(queryClient)
    wasBusy.current = busy
  }, [busy, queryClient])

  const lastRun = summary?.last_run
  let statusLine: string | null = null
  if (summary) {
    if (!lastRun) statusLine = t('categorization.neverRun')
    else if (lastRun.status === 'failed') statusLine = t('categorization.lastRunFailed')
    else if (lastRun.finished_at) {
      const stat = (key: string) => Number(lastRun.stats?.[key] ?? 0) || 0
      statusLine = t('categorization.lastRun', {
        when: relativeFromNow(lastRun.finished_at, locale),
        applied: stat('applied') + stat('memory'),
        review: stat('suggested'),
      })
    }
  }

  return (
    <div>
      <PageHeader
        section={t('nav.groupAccounts')}
        title={t('nav.categorization')}
        action={
          <div className="flex items-center gap-2">
            {canManage && (
              <Button variant="ghost" size="icon" asChild title={t('categorization.settings')}>
                <Link to="/workspace/settings" aria-label={t('categorization.settings')}>
                  <Settings size={16} />
                </Link>
              </Button>
            )}
            {canWrite && (
              <Button onClick={() => run.mutate()} disabled={busy} className="gap-2">
                {busy ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
                {busy ? t('categorization.running') : t('categorization.runNow')}
              </Button>
            )}
          </div>
        }
      />

      {summary && !summary.ai_available && (
        <div className="mb-3 flex items-center gap-1.5 rounded-lg border border-border bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
          <span>{t('categorization.aiOff')}</span>
          {canManage && (
            <>
              <span aria-hidden="true">·</span>
              <Link to="/workspace/settings" className="font-medium text-primary hover:underline">
                {t('categorization.configure')}
              </Link>
            </>
          )}
        </div>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2">
        <Segmented
          value={tab}
          onChange={setTab}
          testIdPrefix="categorization-tab"
          options={[
            { value: 'review', label: t('categorization.tab.review'), count: summary?.pending_groups },
            { value: 'applied', label: t('categorization.tab.applied') },
            { value: 'organize', label: t('categorization.tab.organize') },
          ]}
        />
        {statusLine && (
          <p data-testid="categorization-status" className="text-xs text-muted-foreground">
            {statusLine}
          </p>
        )}
      </div>

      {tab === 'review' && <ReviewTab />}
      {tab === 'applied' && <AppliedTab />}
      {tab === 'organize' && <OrganizeTab />}
    </div>
  )
}
