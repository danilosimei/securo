/** "Para onde foi o dinheiro": the spending of a period at a glance, by
 *  group, as a donut with the total in the middle and a short list beside it
 *  (value, share and a bar). A click on a group shows its categories in the
 *  same card; a click on a category is handed to the page (the Dashboard
 *  opens its transactions, Reports opens Explore). Same numbers as Explore. */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { Cell, Pie, PieChart } from 'recharts'
import { reports, type BreakdownParams, type ReportPeriodParams } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { ReportBlock } from '@/components/reports/common/report-block'
import { blockState } from '@/components/reports/common/block-state'
import { CategoryGlyph } from '@/components/reports/common/category-glyph'
import { formatMoneyShort, formatPercent, usePrefersReducedMotion } from '@/components/reports/common/chart-utils'
import { NEUTRAL_COLOR, useGroupPalette } from '@/components/reports/common/group-colors'
import { glyphOfRow } from '@/components/reports/explore/explore-model'
import { nextStep } from '@/components/reports/explore/drilldown'
import { buildSlices, type Slice } from './where-money-model'

const DONUT = 184

export interface PickedCategory {
  /** A category id, or 'none' for the transactions without one. */
  id: string
  label: string
}

export interface PickedGroup {
  /** A group id, or 'none' for the categories without a group. */
  id: string
  label: string
}

interface WhereMoneyCardProps {
  scope: ReportPeriodParams
  onCategory: (category: PickedCategory, group: PickedGroup | null) => void
  className?: string
}

export function WhereMoneyCard({ scope, onCategory, className }: WhereMoneyCardProps) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const palette = useGroupPalette()
  const reduceMotion = usePrefersReducedMotion()
  const [group, setGroup] = useState<PickedGroup | null>(null)

  const params: BreakdownParams = group ? { level: 'category', group_id: group.id, ...scope } : { level: 'group', ...scope }
  const query = useQuery({
    queryKey: ['reports', 'breakdown', params],
    queryFn: () => reports.breakdown(params),
    // A period switch keeps the old donut up; another level is another list.
    placeholderData: (previous, previousQuery) =>
      (previousQuery?.queryKey[2] as BreakdownParams | undefined)?.group_id === params.group_id
        ? previous
        : undefined,
  })
  const data = query.data
  const stale = query.isPlaceholderData
  const currency = data?.currency ?? 'BRL'
  const slices = data ? buildSlices(data.rows, t('reports.whereMoney.others')) : []

  const colorOf = (slice: Slice) => {
    if (!slice.row) return NEUTRAL_COLOR
    if (!group) return palette.group(slice.row.id, slice.row.label)
    const own = palette.category(slice.row.id, slice.row.label)
    return own === NEUTRAL_COLOR && group.id !== 'none' ? palette.group(group.id) : own
  }

  const open = (slice: Slice) => {
    const row = slice.row
    if (!row) return
    if (group) {
      onCategory({ id: row.id ?? 'none', label: row.label }, group)
      return
    }
    const step = nextStep('group', row)
    if (step && 'group' in step) setGroup({ id: step.group, label: row.label })
    else if (step && 'category' in step) onCategory({ id: step.category, label: row.label }, null)
  }

  const top = slices.find((s) => s.row)
  const title = top
    ? t(group ? 'reports.whereMoney.answerIn' : 'reports.whereMoney.answer', {
        name: top.label,
        share: formatPercent(top.share, locale),
        group: group?.label,
      })
    : t('reports.whereMoney.title')
  const listLabel = group ? t('reports.whereMoney.inGroup', { group: group.label }) : t('reports.whereMoney.byGroup')

  return (
    <ReportBlock
      question={group ? group.label : t('reports.whereMoney.question')}
      title={title}
      state={blockState(query, !!data && slices.length === 0)}
      emptyText={t('reports.whereMoney.empty')}
      stale={stale}
      onRetry={() => query.refetch()}
      skeletonHeight={DONUT}
      className={className}
      testId="block-where-money"
      action={
        group && (
          <button
            type="button"
            onClick={() => setGroup(null)}
            className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ArrowLeft size={13} aria-hidden="true" />
            {t('reports.whereMoney.back')}
          </button>
        )
      }
    >
      {data && (
        <div className="grid items-center gap-5 sm:grid-cols-[auto_minmax(0,1fr)]">
          <div className="relative mx-auto" style={{ width: DONUT, height: DONUT }} aria-hidden="true">
            <PieChart width={DONUT} height={DONUT}>
              <Pie
                data={slices}
                dataKey="value"
                nameKey="label"
                innerRadius="64%"
                outerRadius="100%"
                paddingAngle={slices.length > 1 ? 1.5 : 0}
                stroke="var(--card)"
                strokeWidth={1}
                startAngle={90}
                endAngle={-270}
                isAnimationActive={!reduceMotion}
                onClick={(_, index) => open(slices[index])}
              >
                {slices.map((slice) => (
                  <Cell key={slice.key} fill={colorOf(slice)} cursor={slice.row ? 'pointer' : 'default'} />
                ))}
              </Pie>
            </PieChart>
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
              <span className="text-[11px] text-muted-foreground">{t('reports.whereMoney.total')}</span>
              <span className="text-base font-semibold tabular-nums text-foreground" data-testid="where-money-total">
                {mask(formatMoneyShort(Number(data.total), currency, locale))}
              </span>
            </div>
          </div>

          <ol className="min-w-0 space-y-0.5" aria-label={listLabel}>
            {slices.map((slice) => {
              const share = formatPercent(slice.share, locale)
              const glyph = slice.row ? glyphOfRow(palette, slice.row) : null
              const color = colorOf(slice)
              const body = (
                <>
                  <span className="flex min-w-0 items-center gap-2">
                    {glyph ? (
                      <CategoryGlyph icon={glyph.icon} color={glyph.color} />
                    ) : (
                      <span aria-hidden="true" className="size-3 shrink-0 rounded-full" style={{ background: color }} />
                    )}
                    <span className="truncate text-sm font-medium text-foreground">{slice.label}</span>
                    <span className="ml-auto shrink-0 whitespace-nowrap text-xs tabular-nums">
                      <span className="font-semibold text-foreground">{mask(formatMoneyShort(slice.value, currency, locale))}</span>
                      <span className="ml-1.5 inline-block w-9 text-right text-muted-foreground">{share}</span>
                    </span>
                  </span>
                  <span aria-hidden="true" className="mt-1 block h-1 overflow-hidden rounded-full bg-muted/60">
                    <span className="block h-full rounded-full" style={{ width: `${slice.share * 100}%`, background: color }} />
                  </span>
                </>
              )
              const label = `${slice.label}: ${mask(formatCurrency(slice.value, currency, locale))} (${share})`
              return (
                <li key={slice.key}>
                  {slice.row ? (
                    <button
                      type="button"
                      onClick={() => open(slice)}
                      aria-label={label}
                      className="block w-full rounded-md px-2 py-1.5 text-left transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      {body}
                    </button>
                  ) : (
                    <div className="px-2 py-1.5" aria-label={label}>
                      {body}
                    </div>
                  )}
                </li>
              )
            })}
          </ol>
        </div>
      )}
    </ReportBlock>
  )
}
