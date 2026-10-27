import { useEffect, useRef } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Loader2, Settings, Sparkles } from 'lucide-react'
import { categorization as categorizationApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateCategorizationQueries } from '@/lib/invalidate-queries'
import { formatRelativeFromNow } from '@/lib/relative-time'
import { isCategorizationBusy, useCategorizationSummary } from '@/hooks/use-categorization-summary'
import { useWorkspace } from '@/contexts/workspace-context'
import { useDateLocale } from '@/hooks/use-display-locale'
import { PageHeader } from '@/components/page-header'
import { Segmented } from '@/components/invoice-ui'
import { Button } from '@/components/ui/button'
import { FadePanel } from '@/components/ui/fade-panel'
import { ReviewTab } from '@/components/categorization/review-tab'
import { AppliedTab } from '@/components/categorization/applied-tab'
import { OrganizeTab } from '@/components/categorization/organize-tab'
import { HistoryTab } from '@/components/categorization/history-tab'
import { AskProvider } from '@/components/categorization/ask-provider'
import { Scoreboard } from '@/components/categorization/scoreboard'

type Tab = 'review' | 'applied' | 'organize' | 'history'
const TABS: Tab[] = ['review', 'applied', 'organize', 'history']

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
    else if (isCategorizationBusy(summary)) statusLine = t('categorization.running')
    else if (lastRun.finished_at) {
      const stat = (key: string) => Number(lastRun.stats?.[key] ?? 0) || 0
      // "To review" is the situation now; the run only explains what it did.
      const params = {
        when: formatRelativeFromNow(lastRun.finished_at, locale),
        count: stat('applied') + stat('memory'),
        review: summary.pending_transactions,
      }
      statusLine = t(
        summary.pending_transactions > 0 ? 'categorization.lastRun' : 'categorization.allReviewed',
        params,
      )
    }
  }

  return (
    <div>
      <PageHeader
        section={t('nav.groupDaily')}
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
              <Link to="/workspace/settings#categorization" className="font-medium text-primary hover:underline">
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
            { value: 'history', label: t('categorization.tab.history') },
          ]}
        />
        {statusLine && (
          <p data-testid="categorization-status" className="text-xs text-muted-foreground">
            {statusLine}
          </p>
        )}
        <Scoreboard />
      </div>

      {/* "Ask AI" opens from Review and Applied; its conversations last the visit. */}
      <AskProvider>
        <FadePanel panelKey={tab}>
          {tab === 'review' && <ReviewTab />}
          {tab === 'applied' && <AppliedTab />}
          {tab === 'organize' && <OrganizeTab />}
          {tab === 'history' && <HistoryTab />}
        </FadePanel>
      </AskProvider>
    </div>
  )
}
