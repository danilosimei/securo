/** "O que se repete": the stores the person goes back to, several times a
 *  month, which nobody notices one purchase at a time. One line per store:
 *  its category icon in the group's color, how often ("10× in 4 months,
 *  about 2.5× a month"), what it adds up to, and its last 12 months. A line
 *  opens the store in Explore; "see all stores" opens Explore by store. The
 *  categories bought from most often sit on top, one line each. */
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import type { OverviewRepeat, OverviewResponse, ReportPeriodParams } from '@/lib/api'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { CategoryIcon } from '@/components/category-icon'
import { Button } from '@/components/ui/button'
import { formatMoneyShort } from '@/components/reports/common/chart-utils'
import type { GroupPalette } from '@/components/reports/common/group-colors'
import { MiniTrend } from '@/components/reports/common/mini-trend'
import { exploreLink, tabLink } from '@/components/reports/common/report-url'

interface RepeatsProps {
  data: OverviewResponse
  query: ReportPeriodParams
  currency: string
  locale: string
  palette: GroupPalette
}

export function Repeats({ data, query, currency, locale, palette }: RepeatsProps) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const repeats = data.repeats ?? []
  const categories = data.repeat_categories ?? []
  const money = (v: number) => mask(formatMoneyShort(v, currency, locale))
  const times = (n: number) => n.toLocaleString(locale, { maximumFractionDigits: 1 })

  const storeLink = (r: OverviewRepeat) =>
    exploreLink(
      { ...query, group: r.group_id, category: r.category_id ?? 'none', merchant: r.merchant },
      {
        group: r.group_id ? palette.groupName(r.group_id) : undefined,
        category: r.category,
        merchant: r.label,
      },
      { from: data.period.from, to: data.period.to },
    )
  const allStores = tabLink('explore', query)
  allStores.to += '&view=merchant'

  return (
    <div className="space-y-4" data-testid="repeats">
      {categories.length > 0 && (
        <ul className="grid gap-2 sm:grid-cols-3" data-testid="repeat-categories" aria-label={t('reports.overview.repeats.categories')}>
          {categories.map((c) => {
            const link = exploreLink(
              { ...query, group: c.group_id, category: c.category_id },
              { group: c.group_id ? palette.groupName(c.group_id) : undefined, category: c.category },
            )
            return (
              <li key={c.category_id}>
                <Link
                  {...link}
                  className="flex items-center gap-2.5 rounded-lg border border-border bg-muted/30 px-3 py-2 transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <CategoryIcon icon={palette.categoryIcon(c.category_id)} color={palette.group(c.group_id)} size="sm" />
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium text-foreground">{c.category}</span>
                    <span className="block truncate text-xs tabular-nums text-muted-foreground">
                      {t('reports.overview.repeats.categoryLine', { n: times(c.per_month), value: money(Number(c.month_total)) })}
                    </span>
                  </span>
                </Link>
              </li>
            )
          })}
        </ul>
      )}

      <ul className="divide-y divide-border" data-testid="repeat-stores">
        {repeats.map((r) => {
          const total = r.times * Number(r.avg_purchase)
          const color = palette.group(r.group_id)
          return (
            <li key={r.merchant}>
              <Link
                {...storeLink(r)}
                data-testid="repeat-store"
                aria-label={t('reports.overview.repeats.aria', {
                  name: r.label,
                  times: r.times,
                  months: r.months,
                  month: money(Number(r.month_total)),
                })}
                className="group grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-0.5 rounded-md px-1.5 py-2.5 transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:grid-cols-[auto_minmax(0,1fr)_10rem_9rem_6rem]"
              >
                <CategoryIcon icon={palette.categoryIcon(r.category_id)} color={color} size="sm" className="row-span-2 self-start sm:row-span-1 sm:self-center" />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium text-foreground group-hover:underline group-hover:decoration-foreground/30 group-hover:underline-offset-4">
                    {r.label}
                  </span>
                  <span className="hidden truncate text-[11px] text-muted-foreground sm:block">{r.category}</span>
                </span>
                <span className="col-span-2 col-start-2 row-start-2 text-xs tabular-nums text-muted-foreground sm:col-span-1 sm:col-start-auto sm:row-start-auto">
                  {t('reports.overview.repeats.times', { times: r.times, months: r.months })}
                  <span className="text-muted-foreground/80"> · {t('reports.overview.repeats.perMonth', { n: times(r.per_month) })}</span>
                </span>
                <span className="col-start-3 row-start-1 text-right text-xs tabular-nums sm:col-start-auto sm:row-start-auto">
                  <span className="block font-semibold text-foreground">{t('reports.overview.repeats.monthly', { value: money(Number(r.month_total)) })}</span>
                  <span className="hidden text-muted-foreground sm:block">{t('reports.overview.repeats.inPeriod', { value: money(total) })}</span>
                </span>
                <span className="hidden sm:block" style={{ color }}>
                  <MiniTrend values={r.sparkline} height={28} color={color} />
                </span>
              </Link>
            </li>
          )
        })}
      </ul>

      <Button asChild size="xs" variant="ghost" className="h-7 px-2 text-primary">
        <Link {...allStores} data-testid="repeats-all">
          {t('reports.overview.repeats.seeAll')}
          <ArrowRight aria-hidden="true" />
        </Link>
      </Button>
    </div>
  )
}
