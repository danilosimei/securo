/** Highlights: what is out of the ordinary in the period, one card per rule.
 *  The number leads, the sentence explains it, and the whole card opens the
 *  place in Reports where it can be seen in detail. */
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import {
  ArrowRight,
  CalendarClock,
  ClipboardCheck,
  Coffee,
  Layers,
  type LucideIcon,
  Repeat,
  TrendingUp,
  Zap,
} from 'lucide-react'
import type { HighlightRule, OverviewResponse, ReportPeriodParams } from '@/lib/api'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatMoneyShort, maskMoneyInText } from '@/components/reports/common/chart-utils'
import type { GroupPalette } from '@/components/reports/common/group-colors'
import { highlightLink } from './overview-model'

const ICONS: Record<HighlightRule, LucideIcon> = {
  spike: Zap,
  what_changed: TrendingUp,
  recurring_new_or_up: Repeat,
  recurring_expensive: CalendarClock,
  small_purchases: Coffee,
  heavy_group: Layers,
  to_review: ClipboardCheck,
}

interface HighlightsProps {
  data: OverviewResponse
  query: ReportPeriodParams
  currency: string
  locale: string
  palette: GroupPalette
}

export function Highlights({ data, query, currency, locale, palette }: HighlightsProps) {
  const { t } = useTranslation()
  const { mask, privacyMode, MASK } = usePrivacyMode()
  if (data.highlights.length === 0) {
    return <p className="py-6 text-center text-sm text-muted-foreground">{t('reports.overview.highlights.none')}</p>
  }
  return (
    <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" data-testid="highlights">
      {data.highlights.map((h) => {
        const Icon = ICONS[h.rule] ?? Zap
        const link = highlightLink(h, data, query, palette)
        const value = h.rule === 'to_review' ? h.value : mask(formatMoneyShort(Number(h.value), currency, locale))
        const title = maskMoneyInText(h.title, privacyMode, MASK)
        const compare = maskMoneyInText(h.compare, privacyMode, MASK)
        return (
          <li key={`${h.rule}:${h.title}`} className="min-w-0">
            <Link
              to={link.to}
              state={link.state}
              data-testid={`highlight-${h.rule}`}
              aria-label={`${title}. ${compare}. ${t('reports.overview.highlights.see')}`}
              className="group flex h-full flex-col rounded-xl border border-border bg-card p-4 shadow-sm transition-colors hover:border-primary/40 hover:bg-primary/[0.03] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                <Icon size={13} aria-hidden="true" className="text-primary" />
                {t(`reports.overview.highlights.rule.${h.rule}`)}
              </span>
              <span className="mt-2 text-2xl font-semibold text-foreground">{value}</span>
              <span className="mt-1 text-sm font-medium leading-snug text-foreground">{title}</span>
              {compare && <span className="mt-1 text-xs leading-snug text-muted-foreground">{compare}</span>}
              <span className="mt-auto flex items-center gap-1 pt-3 text-xs font-semibold text-primary">
                {t('reports.overview.highlights.see')}
                <ArrowRight size={13} aria-hidden="true" className="transition-transform group-hover:translate-x-0.5" />
              </span>
            </Link>
          </li>
        )
      })}
    </ul>
  )
}
