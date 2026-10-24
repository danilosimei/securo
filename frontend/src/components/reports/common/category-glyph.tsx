/** The icon of a group or category, drawn in a color and nothing else: no
 *  box, no colored background. Reports put it before a group's or a
 *  category's name so a row reads as the same thing it is on the Categories
 *  page; stores have no icon. Same icon set as `CategoryIcon` (Lucide names,
 *  emoji for categories saved before the icon set, a placeholder otherwise). */
import { CircleHelp } from 'lucide-react'
import { ICON_MAP, isEmoji } from '@/lib/category-icons'
import { cn } from '@/lib/utils'

interface CategoryGlyphProps {
  icon: string | null | undefined
  /** Any CSS color (hex, `var(--…)`, `color-mix(…)`). */
  color: string | null | undefined
  /** Pixels; 14 by default, the size of a table row's text. */
  size?: number
  className?: string
}

const FALLBACK_COLOR = 'var(--muted-foreground)'

export function CategoryGlyph({ icon, color, size = 14, className }: CategoryGlyphProps) {
  const name = icon || 'circle-help'
  if (isEmoji(name)) {
    return (
      <span
        aria-hidden="true"
        data-testid="category-glyph"
        className={cn('inline-flex shrink-0 items-center justify-center', className)}
        style={{ width: size, height: size, fontSize: size - 2, lineHeight: 1 }}
      >
        {name}
      </span>
    )
  }
  const Icon = ICON_MAP[name] ?? CircleHelp
  return (
    <Icon
      aria-hidden="true"
      data-testid="category-glyph"
      size={size}
      strokeWidth={2}
      className={cn('shrink-0', className)}
      style={{ color: color || FALLBACK_COLOR }}
    />
  )
}
