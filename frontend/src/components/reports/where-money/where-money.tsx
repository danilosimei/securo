/** "Para onde foi o dinheiro": the spending of a period at a glance, by
 *  group, as a donut with the total in the middle and a short list beside it
 *  (value, share and a bar). A click on a group shows its categories in the
 *  same card; a click on a category is handed to the page (the Dashboard
 *  opens its transactions, Reports opens Explore). Same numbers as Explore.
 *  Hovering a slice (or its line) lights it up, puts it in the middle of the
 *  donut and, on the donut, opens a tooltip: value, share, change against
 *  the period before, how many transactions and what a click does. */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { Cell, Pie, PieChart } from 'recharts'
import { reports, type BreakdownParams, type ReportPeriodParams } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { ReportBlock, TooltipRow } from '@/components/reports/common/report-block'
import { blockState } from '@/components/reports/common/block-state'
import { CategoryGlyph } from '@/components/reports/common/category-glyph'
import { formatMoneyShort, formatPercent, usePrefersReducedMotion } from '@/components/reports/common/chart-utils'
import { NEUTRAL_COLOR, useGroupPalette } from '@/components/reports/common/group-colors'
import { cn } from '@/lib/utils'
import { glyphOfRow } from '@/components/reports/explore/explore-model'
import { nextStep } from '@/components/reports/explore/drilldown'
import { buildSlices, type Slice } from './where-money-model'

const DONUT = 184
/** Lines of "Others" its tooltip names before "+N". */
const REST_LINES = 5

function formatChange(change: number, locale: string) {
  return new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 0, signDisplay: 'exceptZero' }).format(change)
}

/** More spending is the bad direction. */
function changeTone(change: number) {
  if (change === 0) return 'text-muted-foreground'
  return change > 0 ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-400'
}

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
  /** Null when there is no period to ask for yet (a month still to come). */
  scope: ReportPeriodParams | null
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
  // The slice in focus (from the donut or the list) and, on the donut, where
  // the mouse is, for the tooltip.
  const [hovered, setHovered] = useState<string | null>(null)
  const [pointer, setPointer] = useState<{ x: number; y: number } | null>(null)

  // Without a period the query stays off; the fallback only fills the key.
  const period = scope ?? { period: 'this_month' as const }
  const params: BreakdownParams = group ? { level: 'category', group_id: group.id, ...period } : { level: 'group', ...period }
  const query = useQuery({
    queryKey: ['reports', 'breakdown', params],
    queryFn: () => reports.breakdown(params),
    enabled: scope !== null,
    // A period switch keeps the old donut up; another level is another list.
    placeholderData: (previous, previousQuery) =>
      (previousQuery?.queryKey[2] as BreakdownParams | undefined)?.group_id === params.group_id
        ? previous
        : undefined,
  })
  const data = scope ? query.data : undefined
  const stale = query.isPlaceholderData
  const currency = data?.currency ?? 'BRL'
  // The same array across renders: a new one would replay the donut's
  // animation on every mouse move.
  const othersLabel = t('reports.whereMoney.others')
  const slices = useMemo(() => (data ? buildSlices(data.rows, othersLabel) : []), [data, othersLabel])

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

  const focus = slices.find((s) => s.key === hovered) ?? null
  const shareOf = group ? t('reports.whereMoney.ofGroup', { group: group.label }) : t('reports.whereMoney.ofTotal')
  const goBack = () => {
    setHovered(null)
    setPointer(null)
    setGroup(null)
  }
  const pick = (slice: Slice) => {
    setHovered(null)
    setPointer(null)
    open(slice)
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
      state={scope ? blockState(query, !!data && slices.length === 0) : 'empty'}
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
            onClick={goBack}
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
          <div
            className="relative mx-auto"
            style={{ width: DONUT, height: DONUT }}
            aria-hidden="true"
            onMouseMove={(e) => {
              const box = e.currentTarget.getBoundingClientRect()
              setPointer({ x: e.clientX - box.left, y: e.clientY - box.top })
            }}
            onMouseLeave={() => {
              setPointer(null)
              setHovered(null)
            }}
          >
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
                onClick={(_, index) => pick(slices[index])}
                onMouseEnter={(_, index) => setHovered(slices[index]?.key ?? null)}
                onMouseLeave={() => setHovered(null)}
              >
                {slices.map((slice) => (
                  <Cell
                    key={slice.key}
                    fill={colorOf(slice)}
                    fillOpacity={hovered && hovered !== slice.key ? 0.35 : 1}
                    cursor={slice.row ? 'pointer' : 'default'}
                  />
                ))}
              </Pie>
            </PieChart>
            <div
              className="pointer-events-none absolute inset-[20%] flex flex-col items-center justify-center text-center"
              data-testid="where-money-center"
            >
              {focus ? (
                <>
                  <span className="max-w-full truncate text-[11px] text-muted-foreground">{focus.label}</span>
                  <span className="text-base font-semibold tabular-nums text-foreground">
                    {mask(formatMoneyShort(focus.value, currency, locale))}
                  </span>
                  <span className="text-[11px] tabular-nums text-muted-foreground">{formatPercent(focus.share, locale)}</span>
                </>
              ) : (
                <>
                  <span className="text-[11px] text-muted-foreground">{t('reports.whereMoney.total')}</span>
                  <span className="text-base font-semibold tabular-nums text-foreground" data-testid="where-money-total">
                    {mask(formatMoneyShort(Number(data.total), currency, locale))}
                  </span>
                </>
              )}
            </div>

            {focus && pointer && (
              <SliceTooltip
                slice={focus}
                x={pointer.x}
                y={pointer.y}
                color={colorOf(focus)}
                icon={focus.row ? glyphOfRow(palette, focus.row).icon : null}
                shareOf={shareOf}
                hint={t(group ? 'reports.whereMoney.hintCategory' : 'reports.whereMoney.hintGroup')}
                currency={currency}
                locale={locale}
              />
            )}
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
                <li
                  key={slice.key}
                  onMouseEnter={() => setHovered(slice.key)}
                  onMouseLeave={() => setHovered(null)}
                >
                  {slice.row ? (
                    <button
                      type="button"
                      onClick={() => pick(slice)}
                      onFocus={() => setHovered(slice.key)}
                      onBlur={() => setHovered(null)}
                      aria-label={label}
                      className={cn(
                        'block w-full rounded-md px-2 py-1.5 text-left transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                        hovered === slice.key && 'bg-muted/50',
                      )}
                    >
                      {body}
                    </button>
                  ) : (
                    <div className={cn('rounded-md px-2 py-1.5', hovered === slice.key && 'bg-muted/50')} aria-label={label}>
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

interface SliceTooltipProps {
  slice: Slice
  x: number
  y: number
  color: string
  icon: string | null
  /** "do gasto" / "de Alimentação". */
  shareOf: string
  /** What a click does. */
  hint: string
  currency: string
  locale: string
}

/** The readout of the slice under the mouse, next to the pointer. */
function SliceTooltip({ slice, x, y, color, icon, shareOf, hint, currency, locale }: SliceTooltipProps) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const row = slice.row
  return (
    <div
      className="pointer-events-none absolute z-20 w-max min-w-44 max-w-64 rounded-lg border border-border bg-card px-3 py-2 text-xs shadow-lg"
      style={{ left: x + 14, top: y + 14 }}
      data-testid="where-money-tooltip"
    >
      <p className="mb-1 flex items-center gap-1.5 font-semibold text-foreground">
        {row && <CategoryGlyph icon={icon} color={color} size={13} />}
        <span className="truncate">{slice.label}</span>
      </p>
      <TooltipRow
        color={color}
        value={`${mask(formatCurrency(slice.value, currency, locale))} · ${formatPercent(slice.share, locale)}`}
        label={shareOf}
        strong
      />
      {row && (
        <>
          {row.change !== null ? (
            <div className="flex items-center gap-2 py-0.5">
              <span className={cn('font-semibold tabular-nums', changeTone(row.change))}>{formatChange(row.change, locale)}</span>
              <span className="min-w-0 truncate text-muted-foreground">
                {t('reports.whereMoney.vsPrevious', { value: mask(formatMoneyShort(Number(row.previous_total), currency, locale)) })}
              </span>
            </div>
          ) : (
            <p className="py-0.5 text-muted-foreground">{t('reports.whereMoney.noPrevious')}</p>
          )}
          <p className="py-0.5 text-muted-foreground">{t('reports.whereMoney.count', { n: row.count })}</p>
          <p className="mt-1 border-t border-border pt-1 text-[11px] text-muted-foreground">{hint}</p>
        </>
      )}
      {slice.folded.length > 0 && (
        <div className="mt-1 border-t border-border pt-1">
          {slice.folded.slice(0, REST_LINES).map((folded) => (
            <TooltipRow
              key={folded.id ?? folded.label}
              value={mask(formatMoneyShort(Number(folded.total), currency, locale))}
              label={folded.label}
            />
          ))}
          {slice.folded.length > REST_LINES && (
            <p className="py-0.5 text-muted-foreground">{t('reports.whereMoney.moreRest', { n: slice.folded.length - REST_LINES })}</p>
          )}
        </div>
      )}
    </div>
  )
}
