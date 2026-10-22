import type { LucideIcon } from 'lucide-react'
import { MoreHorizontal, Pencil, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

export type RowAction = {
  label: string
  icon?: LucideIcon
  onSelect: () => void
  disabled?: boolean
}

/**
 * The one way a list row offers its actions: a "⋯" menu with Edit, any extra
 * items, then Delete. Touch target is 40px on a phone, 32px on a wide screen.
 * Deleting only opens the menu item; the page still asks to confirm.
 * @example `<RowActions label={goal.name} onEdit={edit} onDelete={() => setDeleting(goal)} />`
 */
export function RowActions({
  label,
  onEdit,
  onDelete,
  extra = [],
  deleteDisabled,
}: {
  /** Name of the row, read out with the button ("More actions: Rent"). */
  label: string
  onEdit?: () => void
  onDelete?: () => void
  /** Items between Edit and Delete. */
  extra?: RowAction[]
  deleteDisabled?: boolean
}) {
  const { t } = useTranslation()
  if (!onEdit && !onDelete && extra.length === 0) return null
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`${t('common.more')}: ${label}`}
          title={t('common.more')}
          onClick={(event) => event.stopPropagation()}
          className="inline-flex size-10 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/30 data-[state=open]:bg-muted sm:size-8"
        >
          <MoreHorizontal size={16} />
        </button>
      </DropdownMenuTrigger>
      {/* The menu is portalled, but React still bubbles its clicks to a
          clickable row; stop them here. */}
      <DropdownMenuContent align="end" onClick={(event) => event.stopPropagation()}>
        {onEdit && (
          <DropdownMenuItem onSelect={onEdit}>
            <Pencil size={16} />
            {t('common.edit')}
          </DropdownMenuItem>
        )}
        {extra.map((item) => (
          <DropdownMenuItem key={item.label} disabled={item.disabled} onSelect={item.onSelect}>
            {item.icon && <item.icon size={16} />}
            {item.label}
          </DropdownMenuItem>
        ))}
        {onDelete && (onEdit || extra.length > 0) && <DropdownMenuSeparator />}
        {onDelete && (
          <DropdownMenuItem variant="destructive" disabled={deleteDisabled} onSelect={onDelete}>
            <Trash2 size={16} />
            {t('common.delete')}
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
