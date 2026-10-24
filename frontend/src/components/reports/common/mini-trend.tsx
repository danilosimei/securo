/** A small trend line without axes, for cards: 12 months, oldest first, the
 *  last month marked with a dot. It only shows the shape; the numbers are in
 *  the card. A flat series draws a flat line in the middle. */
import { useId } from 'react'
import { cn } from '@/lib/utils'
import { useElementWidth } from './chart-utils'

interface MiniTrendProps {
  values: string[] | number[]
  /** Stroke color (a CSS color); text color by default. */
  color?: string
  height?: number
  className?: string
}

const PAD = 4

export function MiniTrend({ values, color = 'currentColor', height = 40, className }: MiniTrendProps) {
  const [ref, width] = useElementWidth<HTMLDivElement>(120)
  const gradient = useId()
  const nums = values.map(Number).filter((n) => Number.isFinite(n))
  if (nums.length < 2) return null
  const min = Math.min(0, ...nums)
  const max = Math.max(...nums)
  const span = max - min
  const point = (n: number, i: number) => {
    const x = PAD + (i / (nums.length - 1)) * (width - PAD * 2)
    const y = span === 0 ? height / 2 : height - PAD - ((n - min) / span) * (height - PAD * 2)
    return [x, y] as const
  }
  const points = nums.map(point)
  const line = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const [lastX, lastY] = points[points.length - 1]
  const area = `M${points[0][0].toFixed(1)},${height - PAD} L${line.replace(/ /g, ' L')} L${lastX.toFixed(1)},${height - PAD} Z`
  return (
    <div ref={ref} className={cn('w-full', className)} style={{ height }} data-testid="mini-trend">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true" className="overflow-visible">
        <defs>
          <linearGradient id={gradient} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.16} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <path d={area} fill={`url(#${gradient})`} />
        <polyline points={line} fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
        <circle cx={lastX} cy={lastY} r={4} fill={color} stroke="var(--card)" strokeWidth={2} />
      </svg>
    </div>
  )
}
