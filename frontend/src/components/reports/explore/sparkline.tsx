/** Tiny inline trend line (12 months, oldest first). No axes, no lib: it only
 *  shows the shape. A flat series draws a flat line in the middle. */
const W = 60
const H = 18
const PAD = 2

export function Sparkline({ values, className }: { values: string[]; className?: string }) {
  const nums = values.map(Number).filter((n) => Number.isFinite(n))
  if (nums.length < 2) return null
  const min = Math.min(...nums)
  const max = Math.max(...nums)
  const span = max - min
  const points = nums
    .map((n, i) => {
      const x = (i / (nums.length - 1)) * (W - PAD * 2) + PAD
      const y = span === 0 ? H / 2 : H - PAD - ((n - min) / span) * (H - PAD * 2)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
  return (
    <svg
      width={W}
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      aria-hidden="true"
      className={className}
      data-testid="explore-sparkline"
    >
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
