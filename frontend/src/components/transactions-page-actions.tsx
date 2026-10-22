import type { ReactNode } from 'react'
import { ArrowLeftRight, CalendarDays, Copy, Download, List, MoreHorizontal, Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { TransactionsViewSwitcher, type TransactionsViewSwitcherProps } from '@/components/transactions-view-switcher'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

type ViewSwitcherConfig = TransactionsViewSwitcherProps

export type TransactionsPageActionsProps = {
  view: ViewSwitcherConfig
  columnPicker: ReactNode
  exportLabel: string
  exporting: boolean
  onExport: () => void
  onAdd?: () => void
  onDuplicate?: () => void
  onTransfer?: () => void
  testId?: string
}

function DesktopViewAndColumns(props: TransactionsPageActionsProps) {
  return (
    <div className="hidden sm:contents">
      <TransactionsViewSwitcher {...props.view} />
      {props.columnPicker}
    </div>
  )
}

function PrimaryAddAction({ onAdd }: { onAdd?: () => void }) {
  const { t } = useTranslation()
  if (!onAdd) return null
  return (
    <Button className="shrink-0" onClick={onAdd}>
      <Plus />
      {t('transactions.addManual')}
    </Button>
  )
}

/** Export, duplicate and transfer live in the "⋯" menu; on a phone the
 *  List/Calendar choice goes in it too, since there is no room beside "New". */
function MoreMenu(props: TransactionsPageActionsProps) {
  const { t } = useTranslation()
  const changeView = (value: string) => {
    if (value === 'list' || value === 'calendar') props.view.onChange(value)
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="icon" aria-label={t('common.more')} title={t('common.more')}>
          <MoreHorizontal size={16} />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <div className="sm:hidden">
          <DropdownMenuRadioGroup value={props.view.value} onValueChange={changeView}>
            <DropdownMenuRadioItem value="list"><List />{props.view.listLabel}</DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="calendar"><CalendarDays />{props.view.calendarLabel}</DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
          <DropdownMenuSeparator />
        </div>
        <DropdownMenuItem disabled={props.exporting} onClick={props.onExport}><Download size={16} className="mr-2" />{props.exportLabel}</DropdownMenuItem>
        {props.onDuplicate && <DropdownMenuItem onClick={props.onDuplicate}><Copy size={16} className="mr-2" />{t('transactions.duplicate')}</DropdownMenuItem>}
        {props.onTransfer && <DropdownMenuItem onClick={props.onTransfer}><ArrowLeftRight size={16} className="mr-2" />{t('transactions.transfer')}</DropdownMenuItem>}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/**
 * The header actions of Transactions: List/Calendar and columns, the main
 * "New" button, and a "⋯" menu with export, duplicate and transfer. The month
 * is not here: it goes in the header's `period` slot, like on every page.
 * @example `<TransactionsPageActions view={view} columnPicker={picker} exportLabel="Export" exporting={false} onExport={exportCsv} />`
 */
export function TransactionsPageActions(props: TransactionsPageActionsProps) {
  return (
    <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto sm:justify-end" data-testid={props.testId}>
      <DesktopViewAndColumns {...props} />
      <PrimaryAddAction onAdd={props.onAdd} />
      <MoreMenu {...props} />
    </div>
  )
}
