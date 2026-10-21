/** How far categorization got, in one line next to the page's status line
 *  (every tab): "N transactions · X% decided · Y% to review", plus "Z%
 *  waiting for the AI" when some are. A thin bar draws the same split for a
 *  glance; the (i) explains what is left out of the base (transfers between
 *  accounts, pending rows, opening balances). */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Info } from 'lucide-react'
import { categorization as categorizationApi } from '@/lib/api'
import { scoreboardParts } from '@/lib/categorization-scoreboard'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

export function Scoreboard({ className }: { className?: string }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const [about, setAbout] = useState(false)
  // Under ['categorization']: `invalidateCategorizationQueries` refreshes it.
  const { data } = useQuery({ queryKey: ['categorization', 'scoreboard'], queryFn: categorizationApi.scoreboard })

  if (!data || data.total <= 0) return null
  const parts = scoreboardParts(data)
  const excluded = t('categorization.scoreboard.excluded')
  const segments = [
    { key: 'decided', value: parts.decided, className: 'bg-emerald-500' },
    { key: 'review', value: parts.toReview, className: 'bg-amber-500' },
    { key: 'waiting', value: parts.waiting, className: 'bg-muted-foreground/30' },
  ]

  return (
    <div className={cn('flex min-w-0 items-center gap-2 text-xs text-muted-foreground', className)}>
      <span aria-hidden="true" className="flex h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-muted">
        {segments.map((s) => (s.value > 0 ? <span key={s.key} className={s.className} style={{ width: `${s.value}%` }} /> : null))}
      </span>
      <p data-testid="categorization-scoreboard" className="tabular-nums">
        {t('categorization.scoreboard.summary', {
          count: data.total,
          total: data.total.toLocaleString(locale),
          decided: parts.decided,
          review: parts.toReview,
        })}
        {data.waiting > 0 && t('categorization.scoreboard.waiting', { waiting: parts.waiting })}
      </p>
      <TooltipProvider>
        <Tooltip open={about} onOpenChange={setAbout}>
          <TooltipTrigger asChild>
            <button
              type="button"
              aria-label={excluded}
              // A tap opens it too (a tooltip alone is hover and focus only).
              onClick={(e) => {
                e.preventDefault()
                setAbout((v) => !v)
              }}
              className="-m-1.5 inline-flex shrink-0 items-center justify-center rounded-full p-1.5 text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40"
            >
              <Info size={13} aria-hidden="true" />
            </button>
          </TooltipTrigger>
          <TooltipContent>{excluded}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  )
}
