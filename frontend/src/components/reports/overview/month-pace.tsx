/** "How is the month going": the current month so far against a normal
 *  month at the same day, as a band across the highlights. One bar: what was
 *  spent so far, a lighter stretch up to where the month ends at this pace,
 *  and a mark where a normal month would be by today. */
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { ArrowRight, Gauge } from 'lucide-react'
import type { OverviewHighlight, OverviewResponse, ReportPeriodParams } from '@/lib/api'
import { cn } from '@/lib/utils'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { Button } from '@/components/ui/button'
import { formatMoneyShort, maskMoneyInText } from '@/components/reports/common/chart-utils'
import type { GroupPalette } from '@/components/reports/common/group-colors'
import { TONE_STYLE } from './tone'
import { reportLinkFrom } from './overview-model'

interface MonthPaceProps {
  highlight: OverviewHighlight
  data: OverviewResponse
  query: ReportPeriodParams
  currency: string
  locale: string
  palette: GroupPalette
}

export function MonthPace({ highlight: h, data, query, currency, locale, palette }: MonthPaceProps) {
  const { t } = useTranslation()
  const { mask, privacyMode, MASK } = usePrivacyMode()
  const tone = TONE_STYLE[h.tone] ?? TONE_STYLE.info
  const money = (v: number) => mask(formatMoneyShort(v, currency, locale))
  const spent = Number(h.value)
  // The server sends these three, in this order.
  const [normalByToday, projection, normalMonth] = (h.drivers ?? []).map((d) => Number(d.value))
  const scale = Math.max(spent, projection || 0, normalMonth || 0) * 1.04 || 1
  const pct = (v: number) => `${Math.min(100, Math.max(0, (v / scale) * 100))}%`
  const action = h.actions?.[0]
  const title = maskMoneyInText(h.title, privacyMode, MASK)
  const toneName = t(`reports.overview.highlights.tone.${h.tone}`)

  return (
    <section
      data-testid="month-pace"
      data-tone={h.tone}
      aria-label={`${t('reports.overview.pace.title')}: ${title}`}
      className="relative overflow-hidden rounded-xl border border-border bg-card p-4 shadow-sm sm:p-5"
    >
      <span aria-hidden="true" className={cn('absolute inset-y-0 left-0 w-1', tone.strip)} />
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:gap-8">
        <div className="min-w-0 lg:w-[42%]">
          <div className="flex items-center gap-2">
            <span className={cn('flex size-7 shrink-0 items-center justify-center rounded-full', tone.chip)}>
              <Gauge size={14} aria-hidden="true" />
            </span>
            <span className="text-xs font-medium text-muted-foreground">{t('reports.overview.pace.title')}</span>
            <span className={cn('text-[11px] font-semibold', tone.text)}>· {toneName}</span>
          </div>
          <p className="mt-2 text-base font-medium leading-snug text-foreground text-pretty">{title}</p>
          <p className="mt-0.5 text-sm text-muted-foreground">{maskMoneyInText(h.compare, privacyMode, MASK)}</p>
        </div>

        <div className="min-w-0 flex-1">
          <div
            role="img"
            aria-label={t('reports.overview.pace.aria', {
              spent: money(spent),
              normal: money(normalByToday ?? 0),
              projection: money(projection ?? 0),
            })}
            className="relative mt-5 h-3 rounded-full bg-muted"
          >
            {projection > spent && (
              <span
                aria-hidden="true"
                className={cn('absolute inset-y-0 left-0 rounded-full opacity-25', tone.strip)}
                style={{ width: pct(projection) }}
              />
            )}
            <span
              aria-hidden="true"
              data-testid="pace-spent"
              className={cn('absolute inset-y-0 left-0 rounded-full', tone.strip)}
              style={{ width: pct(spent) }}
            />
            {normalByToday > 0 && (
              <span
                aria-hidden="true"
                className="absolute -top-1.5 -bottom-1.5 w-0.5 -translate-x-1/2 rounded-full bg-foreground"
                style={{ left: pct(normalByToday) }}
              >
                <span className="absolute bottom-full left-1/2 mb-1 -translate-x-1/2 whitespace-nowrap text-[11px] font-medium text-muted-foreground">
                  {t('reports.overview.pace.normal')}
                </span>
              </span>
            )}
          </div>
          <dl className="mt-3 grid gap-1.5 text-xs sm:grid-cols-3 sm:gap-2">
            <div className="flex items-center justify-between gap-2 sm:block">
              <dt className="flex items-center gap-1.5 text-muted-foreground">
                <span aria-hidden="true" className={cn('size-2 rounded-full', tone.strip)} />
                {t('reports.overview.pace.spent')}
              </dt>
              <dd className="font-semibold tabular-nums text-foreground sm:mt-0.5">{money(spent)}</dd>
            </div>
            <div className="flex items-center justify-between gap-2 sm:block">
              <dt className="flex items-center gap-1.5 text-muted-foreground">
                <span aria-hidden="true" className="h-2.5 w-0.5 rounded-full bg-foreground" />
                {t('reports.overview.pace.normal')}
              </dt>
              <dd className="font-semibold tabular-nums text-foreground sm:mt-0.5">{money(normalByToday ?? 0)}</dd>
            </div>
            <div className="flex items-center justify-between gap-2 sm:block">
              <dt className="flex items-center gap-1.5 text-muted-foreground">
                <span aria-hidden="true" className={cn('size-2 rounded-full opacity-30', tone.strip)} />
                {t('reports.overview.pace.projection')}
              </dt>
              <dd className="font-semibold tabular-nums text-foreground sm:mt-0.5">{money(projection ?? 0)}</dd>
            </div>
          </dl>
        </div>

        {action && (
          <Button asChild size="xs" variant="secondary" className="h-7 self-start px-2.5 lg:self-center">
            <Link {...reportLinkFrom(action.link, data, query, palette)} data-testid="month-pace-action">
              {action.label}
              <ArrowRight aria-hidden="true" />
            </Link>
          </Button>
        )}
      </div>
    </section>
  )
}
