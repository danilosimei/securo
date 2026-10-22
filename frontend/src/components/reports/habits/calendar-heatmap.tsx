/** Spending day by day, as a calendar: one cell per day, Monday-first weeks
 *  as columns, darker where more was spent (one hue, four steps over the days
 *  with spending). Arrow keys move between days; Enter or a click opens that
 *  day's transactions. Wide periods scroll inside their own box on a phone. */
import { useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { ChartTooltip, TooltipRow } from '@/components/reports/common/report-block'
import { useElementWidth } from '@/components/reports/common/chart-utils'
import { dayTransactionsLink } from '@/components/reports/common/report-url'
import { calendarWeeks, heatSteps, weekdayName } from './habits-model'

const MIN_CELL = 10
const MAX_CELL = 16
const GAP = 3
const LEFT = 26
const TOP = 16

const STEP_COLORS = [
  'var(--muted)',
  'color-mix(in oklab, var(--primary) 28%, var(--muted))',
  'color-mix(in oklab, var(--primary) 52%, var(--muted))',
  'color-mix(in oklab, var(--primary) 76%, var(--muted))',
  'var(--primary)',
]

interface HeatmapProps {
  from: string
  to: string
  days: { date: string; total: string }[]
  currency: string
  locale: string
  ariaLabel: string
}

export function CalendarHeatmap({ from, to, days, currency, locale, ariaLabel }: HeatmapProps) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const navigate = useNavigate()
  const weeks = useMemo(() => calendarWeeks(from, to, days), [from, to, days])
  const cells = useMemo(() => weeks.flat().filter((c): c is { date: string; value: number } => c !== null), [weeks])
  const steps = useMemo(() => heatSteps(cells.map((c) => c.value)), [cells])
  const [focus, setFocus] = useState(() => cells.length - 1)
  const [active, setActive] = useState<string | null>(null)
  const refs = useRef(new Map<string, SVGGElement>())
  const [scrollLeft, setScrollLeft] = useState(0)
  const [outer, outerWidth] = useElementWidth<HTMLDivElement>()

  const step = (v: number) => (v <= 0 ? 0 : v <= steps[0] ? 1 : v <= steps[1] ? 2 : v <= steps[2] ? 3 : 4)
  const CELL = Math.max(MIN_CELL, Math.min(MAX_CELL, Math.floor((outerWidth - LEFT) / Math.max(1, weeks.length)) - GAP))
  const width = LEFT + weeks.length * (CELL + GAP)
  const height = TOP + 7 * (CELL + GAP)
  const position = new Map<string, { x: number; y: number }>()
  weeks.forEach((week, w) =>
    week.forEach((cell, d) => {
      if (cell) position.set(cell.date, { x: LEFT + w * (CELL + GAP), y: TOP + d * (CELL + GAP) })
    }),
  )
  // Month names above the first week that holds the month's day 1.
  const monthMarks = weeks
    .map((week, w) => ({ w, first: week.find((c) => c && c.date.endsWith('-01')) }))
    .filter((m) => m.first)
  const open = (date: string) => {
    const link = dayTransactionsLink(date)
    navigate(link.to)
  }
  const move = (delta: number) => {
    const next = Math.max(0, Math.min(cells.length - 1, focus + delta))
    setFocus(next)
    refs.current.get(cells[next].date)?.focus()
  }
  const activeCell = active ? cells.find((c) => c.date === active) : undefined
  const activePos = active ? position.get(active) : undefined
  const dayLabel = (iso: string) =>
    new Date(`${iso}T00:00:00`).toLocaleDateString(locale, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })

  return (
    <div ref={outer} className="relative">
      <div className="-mx-1 overflow-x-auto px-1 pb-1" onScroll={(e) => setScrollLeft(e.currentTarget.scrollLeft)} onMouseLeave={() => setActive(null)}>
        <svg width={width} height={height} role="group" aria-label={ariaLabel} data-testid="habits-calendar" className="block">
          <g aria-hidden="true">
            {monthMarks.map(({ w, first }) => (
              <text key={first!.date} x={LEFT + w * (CELL + GAP)} y={10} fontSize={10} fill="var(--muted-foreground)">
                {new Date(`${first!.date}T00:00:00`).toLocaleDateString(locale, { month: 'short' }).replace('.', '')}
              </text>
            ))}
            {[0, 2, 4].map((d) => (
              <text key={d} x={0} y={TOP + d * (CELL + GAP) + CELL - 2} fontSize={9} fill="var(--muted-foreground)">
                {weekdayName(d, locale)}
              </text>
            ))}
          </g>
          {cells.map((cell, i) => {
            const pos = position.get(cell.date)!
            return (
              <g
                key={cell.date}
                ref={(el) => {
                  if (el) refs.current.set(cell.date, el)
                  else refs.current.delete(cell.date)
                }}
                role="button"
                tabIndex={i === focus ? 0 : -1}
                data-testid="calendar-day"
                data-date={cell.date}
                aria-label={t('reports.habits.calendar.dayAria', {
                  day: dayLabel(cell.date),
                  total: mask(formatCurrency(cell.value, currency, locale)),
                })}
                className="cursor-pointer outline-none [&:focus-visible>rect]:stroke-[var(--ring)]"
                onMouseEnter={() => setActive(cell.date)}
                onFocus={() => {
                  setFocus(i)
                  setActive(cell.date)
                }}
                onBlur={() => setActive(null)}
                onClick={() => open(cell.date)}
                onKeyDown={(e) => {
                  const keys: Record<string, number> = { ArrowRight: 7, ArrowLeft: -7, ArrowDown: 1, ArrowUp: -1 }
                  if (e.key in keys) {
                    e.preventDefault()
                    move(keys[e.key])
                  } else if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    open(cell.date)
                  }
                }}
              >
                <rect x={pos.x} y={pos.y} width={CELL} height={CELL} rx={2.5} style={{ fill: STEP_COLORS[step(cell.value)] }} stroke="transparent" strokeWidth={2} />
              </g>
            )
          })}
        </svg>
      </div>
        {activeCell && activePos && (
          <ChartTooltip x={activePos.x - scrollLeft} y={activePos.y + CELL + 6} width={outerWidth}>
            <p className="mb-1 font-semibold capitalize text-foreground">{dayLabel(activeCell.date)}</p>
            <TooltipRow label={t('reports.habits.calendar.spent')} value={mask(formatCurrency(activeCell.value, currency, locale))} strong />
            <p className="mt-1 text-[11px] font-medium text-primary">{t('reports.habits.calendar.openHint')}</p>
          </ChartTooltip>
        )}
      <div className="mt-2 flex items-center justify-end gap-1 text-[11px] text-muted-foreground" aria-hidden="true">
        {t('reports.habits.calendar.less')}
        {STEP_COLORS.map((c) => (
          <span key={c} className="h-2.5 w-2.5 rounded-[2px]" style={{ background: c }} />
        ))}
        {t('reports.habits.calendar.more')}
      </div>
    </div>
  )
}
