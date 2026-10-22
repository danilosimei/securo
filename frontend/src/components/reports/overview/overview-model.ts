/** Pure helpers of the Overview: the month that spiked, the biggest changes
 *  and where each highlight card leads. */
import type { OverviewChange, OverviewHighlight, OverviewMonth, OverviewResponse, ReportPeriodParams } from '@/lib/api'
import { isReportPeriod } from '@/lib/report-period'
import type { GroupPalette } from '@/components/reports/common/group-colors'
import { exploreLink, tabLink, type ReportLink } from '@/components/reports/common/report-url'

/** A month at this multiple of the median month (or more) is a spike. */
export const SPIKE_MULTIPLE = 1.5

/** The month that stands out most, if any passes the spike line. */
export function peakMonth(months: OverviewMonth[]) {
  const median = Number(months[0]?.median_spending ?? 0)
  const peak = [...months].sort((a, b) => Number(b.spending) - Number(a.spending))[0]
  if (!peak) return null
  const multiple = median > 0 ? Number(peak.spending) / median : null
  return { month: peak, multiple, spike: multiple !== null && multiple >= SPIKE_MULTIPLE }
}

export function changesSummary(changes: OverviewChange[]) {
  const ups = changes.filter((c) => Number(c.delta) > 0)
  const downs = changes.filter((c) => Number(c.delta) < 0)
  const top = (list: OverviewChange[]) =>
    [...list].sort((a, b) => Math.abs(Number(b.delta)) - Math.abs(Number(a.delta)))[0]
  return { up: top(ups), down: top(downs) }
}

/** Where a highlight card leads. The server's link names Explore and its
 *  filters (the spike's is the month that spiked); a few rules have a better
 *  home of their own. */
export function highlightLink(
  highlight: OverviewHighlight,
  data: OverviewResponse,
  query: ReportPeriodParams,
  palette?: GroupPalette,
): ReportLink {
  const link = highlight.link ?? {}
  if (highlight.rule === 'to_review') return { to: '/categorization' }
  if (highlight.rule === 'recurring_new_or_up' && !link.merchant) return tabLink('recurring', query)
  if (link.tab && link.tab !== 'explore') return tabLink(link.tab, query)
  const period = isReportPeriod(link.period) ? link.period : query.period
  const group = link.group_id ?? (link.category_id ? palette?.groupOfCategory(link.category_id) : null) ?? null
  return exploreLink(
    {
      period,
      from: link.from ?? query.from,
      to: link.to ?? query.to,
      group,
      category: link.category_id,
      merchant: link.merchant,
    },
    {
      group: group ? palette?.groupName(group) : undefined,
      category: link.category_id ? palette?.categoryName(link.category_id) : undefined,
    },
    { from: data.period.from, to: data.period.to },
  )
}
