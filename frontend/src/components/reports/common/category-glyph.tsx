/** The icon a group or category has on the Categories page, drawn bare in a
 *  color (no tile behind it), to sit before its name in Reports. */
import { CircleHelp } from 'lucide-react'
import { ICON_MAP, isEmoji } from '@/lib/category-icons'

interface CategoryGlyphProps {
  icon: string | null | undefined
  color: string
  size?: number
}

export function CategoryGlyph({ icon, color, size = 16 }: CategoryGlyphProps) {
  const name = icon || 'circle-help'
  if (isEmoji(name)) {
    return (
      <span aria-hidden="true" className="inline-flex shrink-0 items-center justify-center" style={{ width: size, height: size, fontSize: size - 2, lineHeight: 1 }}>
        {name}
      </span>
    )
  }
  const Icon = ICON_MAP[name] ?? CircleHelp
  return <Icon aria-hidden="true" data-testid="category-glyph" size={size} strokeWidth={2} className="shrink-0" style={{ color }} />
}
