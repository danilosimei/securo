/** How far categorization got, in one line next to the page's status line
 *  (every tab): "N transactions · X% decided · Y to review" ("all reviewed"
 *  at zero), plus "Z waiting for the AI" when some are. A thin bar draws the
 *  decided share, with a sliver that stays visible while anything is left to
 *  review; the (i) explains what is left out of the base (transfers between
 *  accounts, pending rows, opening balances). */
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Info } from 'lucide-react'
import { categorization as categorizationApi } from '@/lib/api'
import { decidedPercent, scoreboardBar } from '@/lib/categorization-scoreboard'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

export function Scoreboard({ className }: { className?: string }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const [about, setAbout] = useState(false)
  // Whether the tip was open when the press began: the trigger closes it on
  // pointer down, so the click must not read the state after that.
  const openAtPress = useRef<boolean | null>(null)
  // Under ['categorization']: `invalidateCategorizationQueries` refreshes it.
  const { data } = useQuery({ queryKey: ['categorization', 'scoreboard'], queryFn: categorizationApi.scoreboard })

  if (!data || data.total <= 0) return null
  const num = (n: number) => n.toLocaleString(locale)
  const bar = scoreboardBar(data)

  return (
    <div className={cn('flex min-w-0 items-center gap-2 text-xs text-muted-foreground', className)}>
      <span aria-hidden="true" className="flex h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-muted">
        {bar.decided > 0 && <span data-bar="decided" className="min-w-0 bg-primary" style={{ width: `${bar.decided}%` }} />}
        {data.to_review > 0 && (
          // At least a few pixels, so "something to review" never disappears.
          <span data-bar="review" className="shrink-0 bg-amber-500" style={{ width: `${bar.toReview}%`, minWidth: 3 }} />
        )}
      </span>
      <p data-testid="categorization-scoreboard" className="tabular-nums">
        {t('categorization.scoreboard.summary', {
          count: data.total,
          total: num(data.total),
          decided: decidedPercent(data),
        })}
        {data.to_review > 0
          ? t('categorization.scoreboard.review', { count: data.to_review, review: num(data.to_review) })
          : t('categorization.scoreboard.allReviewed')}
        {data.waiting > 0 && t('categorization.scoreboard.waiting', { count: data.waiting, waiting: num(data.waiting) })}
      </p>
      <TooltipProvider>
        <Tooltip open={about} onOpenChange={setAbout}>
          <TooltipTrigger asChild>
            <button
              type="button"
              aria-label={t('categorization.scoreboard.about')}
              onPointerDown={() => {
                openAtPress.current = about
              }}
              // A tap toggles it too (a tooltip alone is hover and focus only).
              onClick={(e) => {
                e.preventDefault()
                const wasOpen = openAtPress.current ?? about
                openAtPress.current = null
                setAbout(!wasOpen)
              }}
              className="-m-1.5 inline-flex shrink-0 items-center justify-center rounded-full p-1.5 text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40"
            >
              <Info size={13} aria-hidden="true" />
            </button>
          </TooltipTrigger>
          <TooltipContent>{t('categorization.scoreboard.excluded')}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  )
}
