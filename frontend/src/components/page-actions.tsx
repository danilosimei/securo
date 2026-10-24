import { MoreHorizontal } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import type { RowAction } from '@/components/ui/row-actions'

/**
 * What goes in a page header's `action`: the main "+ New" button, with every
 * secondary action (export, import, packs...) tucked in a "⋯" menu beside it.
 * @example `<PageActions primary={<Button>...</Button>} items={[{ label, icon, onSelect }]} />`
 */
export function PageActions({
  primary,
  items = [],
  testId,
}: {
  primary?: React.ReactNode
  items?: RowAction[]
  testId?: string
}) {
  const { t } = useTranslation()
  if (!primary && items.length === 0) return null
  return (
    <div className="flex items-center gap-2" data-testid={testId}>
      {primary}
      {items.length > 0 && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="icon" aria-label={t('common.more')} title={t('common.more')}>
              <MoreHorizontal size={16} />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {items.map((item) => (
              <DropdownMenuItem key={item.label} disabled={item.disabled} onSelect={item.onSelect}>
                {item.icon && <item.icon size={16} />}
                {item.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  )
}
