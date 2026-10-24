/** Highlights: what is out of the ordinary, one card per rule. The tone
 *  (worth a look, a chance to save, good to know) colors a thin strip and the
 *  icon and is also written out; the number leads, a mini chart shows the
 *  last 12 months, "what pulled it" names the parts behind the number, and
 *  the buttons say what to do next. How the current month is going comes
 *  first, as a band across the whole width. */
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import {
  ArrowRight,
  CalendarClock,
  ClipboardCheck,
  Coffee,
  Gauge,
  Layers,
  type LucideIcon,
  MapPin,
  Repeat,
  TrendingUp,
  Zap,
} from 'lucide-react'
import type { HighlightRule, OverviewHighlight, OverviewResponse, ReportPeriodParams } from '@/lib/api'
import { cn } from '@/lib/utils'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { Button } from '@/components/ui/button'
import { formatMoneyShort, maskMoneyInText } from '@/components/reports/common/chart-utils'
import type { GroupPalette } from '@/components/reports/common/group-colors'
import { MiniTrend } from '@/components/reports/common/mini-trend'
import { reportLinkFrom } from './overview-model'
import { MonthPace } from './month-pace'
import { TONE_STYLE } from './tone'

const ICONS: Record<HighlightRule, LucideIcon> = {
  month_pace: Gauge,
  spike: Zap,
  what_changed: TrendingUp,
  recurring_changed: Repeat,
  recurring_expensive: CalendarClock,
  small_purchases: Coffee,
  heavy_group: Layers,
  frequent_spot: MapPin,
  to_review: ClipboardCheck,
}

/** Rules whose drivers are changes, written with a sign. */
const SIGNED = new Set<HighlightRule>(['spike', 'what_changed', 'recurring_changed'])

interface HighlightsProps {
  data: OverviewResponse
  query: ReportPeriodParams
  currency: string
  locale: string
  palette: GroupPalette
}

export function Highlights({ data, query, currency, locale, palette }: HighlightsProps) {
  const { t } = useTranslation()
  const pace = data.highlights.find((h) => h.rule === 'month_pace')
  const cards = data.highlights.filter((h) => h.rule !== 'month_pace')
  if (!pace && cards.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-border py-8 text-center text-sm text-muted-foreground">
        {t('reports.overview.highlights.none')}
      </p>
    )
  }
  return (
    <div className="space-y-3">
      {pace && <MonthPace highlight={pace} data={data} query={query} currency={currency} locale={locale} palette={palette} />}
      {cards.length > 0 && (
        <ul
          data-testid="highlights"
          className={cn('grid gap-3 sm:grid-cols-2', cards.length === 3 && 'xl:grid-cols-3')}
        >
          {cards.map((h) => (
            <li key={`${h.rule}:${h.title}`} className="min-w-0">
              <HighlightCard highlight={h} data={data} query={query} currency={currency} locale={locale} palette={palette} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

interface CardProps extends Omit<HighlightsProps, 'data'> {
  highlight: OverviewHighlight
  data: OverviewResponse
}

function HighlightCard({ highlight: h, data, query, currency, locale, palette }: CardProps) {
  const { t } = useTranslation()
  const { mask, privacyMode, MASK } = usePrivacyMode()
  const Icon = ICONS[h.rule] ?? Zap
  const tone = TONE_STYLE[h.tone] ?? TONE_STYLE.info
  const money = (v: number, signed = false) => {
    const text = mask(formatMoneyShort(Math.abs(v), currency, locale))
    if (!signed || v === 0) return text
    return `${v > 0 ? '+' : '−'}${text}`
  }
  const value = h.rule === 'to_review' ? h.value : money(Number(h.value))
  const title = maskMoneyInText(h.title, privacyMode, MASK)
  const compare = maskMoneyInText(h.compare, privacyMode, MASK)
  const drivers = h.drivers ?? []
  // A fixed cost's "before" and "after" read as one line: R$ 20 → R$ 30.
  const beforeAfter =
    (h.rule === 'recurring_changed' || h.rule === 'recurring_expensive') &&
    drivers.length === 2 &&
    drivers.every((d) => !d.link)
  const [primary, secondary] = h.actions ?? []
  const toneName = t(`reports.overview.highlights.tone.${h.tone}`)

  return (
    <article
      data-testid={`highlight-${h.rule}`}
      data-tone={h.tone}
      aria-label={`${toneName}: ${title}`}
      className="relative flex h-full flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm"
    >
      <span aria-hidden="true" className={cn('absolute inset-x-0 top-0 h-1', tone.strip)} />
      <div className="flex flex-1 flex-col p-4 pt-5">
        <div className="flex items-center justify-between gap-2">
          <span className="flex min-w-0 items-center gap-2">
            <span className={cn('flex size-7 shrink-0 items-center justify-center rounded-full', tone.chip)}>
              <Icon size={14} aria-hidden="true" />
            </span>
            <span className="truncate text-xs font-medium text-muted-foreground">
              {t(`reports.overview.highlights.rule.${h.rule}`)}
            </span>
          </span>
          <span className={cn('shrink-0 text-[11px] font-semibold', tone.text)}>{toneName}</span>
        </div>

        <div className="mt-3 flex items-end justify-between gap-3">
          <p className="text-2xl font-semibold leading-none text-foreground" data-testid="highlight-value">
            {value}
          </p>
          {h.sparkline && h.sparkline.length > 1 && (
            <div className={cn('w-24 shrink-0 sm:w-28', tone.text)}>
              <MiniTrend values={h.sparkline} height={36} />
            </div>
          )}
        </div>
        <p className="mt-2 text-sm font-medium leading-snug text-foreground text-pretty">{title}</p>
        {compare && <p className="mt-0.5 text-xs leading-snug text-muted-foreground">{compare}</p>}

        {drivers.length > 0 && (
          <div className="mt-3 border-t border-border/70 pt-2.5" data-testid="highlight-drivers">
            {beforeAfter ? (
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <span>{drivers[0].label}</span>
                <span className="font-semibold tabular-nums text-foreground">{money(Number(drivers[0].value))}</span>
                <ArrowRight size={12} aria-hidden="true" />
                <span>{drivers[1].label}</span>
                <span className="font-semibold tabular-nums text-foreground">{money(Number(drivers[1].value))}</span>
              </p>
            ) : (
              <>
                <p className="mb-1 text-[11px] font-medium text-muted-foreground">
                  {t(`reports.overview.highlights.drivers.${h.rule}`, {
                    defaultValue: t('reports.overview.highlights.drivers.default'),
                  })}
                </p>
                <ul className="space-y-0.5">
                  {drivers.map((d) => {
                    const amount = money(Number(d.value), SIGNED.has(h.rule))
                    const label = maskMoneyInText(d.label, privacyMode, MASK)
                    const inner = (
                      <>
                        <span className="min-w-0 truncate">{label}</span>
                        <span className="shrink-0 font-semibold tabular-nums text-foreground">{amount}</span>
                      </>
                    )
                    return (
                      <li key={d.label}>
                        {d.link ? (
                          <Link
                            {...reportLinkFrom(d.link, data, query, palette)}
                            className="-mx-1 flex items-center justify-between gap-2 rounded px-1 py-0.5 text-xs text-foreground/80 underline decoration-foreground/20 underline-offset-4 hover:bg-muted/60 hover:decoration-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          >
                            {inner}
                          </Link>
                        ) : (
                          <span className="flex items-center justify-between gap-2 py-0.5 text-xs text-foreground/80">{inner}</span>
                        )}
                      </li>
                    )
                  })}
                </ul>
              </>
            )}
          </div>
        )}

        {primary && (
          <div className="mt-auto flex flex-wrap items-center gap-2 pt-4">
            <Button asChild size="xs" variant="secondary" className="h-7 px-2.5">
              <Link {...reportLinkFrom(primary.link, data, query, palette)} data-testid={`highlight-action-${primary.kind}`}>
                {primary.label}
                <ArrowRight aria-hidden="true" />
              </Link>
            </Button>
            {secondary && (
              <Button asChild size="xs" variant="ghost" className="h-7 px-2 text-muted-foreground">
                <Link {...reportLinkFrom(secondary.link, data, query, palette)} data-testid={`highlight-action-${secondary.kind}`}>
                  {secondary.label}
                </Link>
              </Button>
            )}
          </div>
        )}
      </div>
    </article>
  )
}
