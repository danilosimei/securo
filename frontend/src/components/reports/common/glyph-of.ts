/** Which icon and color sit before a group or category name in Reports: the
 *  icon from the Categories page, always in the group's color (a category in
 *  its group's). Null for rows without an id (no group, no category, stores)
 *  and while the icon is unknown, so nothing flashes a placeholder. */
import type { GroupPalette } from './group-colors'

export interface GlyphSpec {
  icon: string
  color: string
}

export function groupGlyph(palette: GroupPalette, id: string | null | undefined, label?: string): GlyphSpec | null {
  const icon = id ? palette.groupIcon(id) : null
  return icon ? { icon, color: palette.group(id, label) } : null
}

export function categoryGlyph(palette: GroupPalette, id: string | null | undefined, groupLabel?: string): GlyphSpec | null {
  const icon = id ? palette.categoryIcon(id) : null
  return icon && id ? { icon, color: palette.group(palette.groupOfCategory(id), groupLabel) } : null
}
