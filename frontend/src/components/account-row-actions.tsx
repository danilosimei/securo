import { Archive } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { RowActions } from '@/components/ui/row-actions'

export type AccountRowActionsProps = {
  accountName: string
  onEdit: () => void
  onClose: () => void
  onDelete?: () => void
  deletePending: boolean
}

/**
 * Edit, close and delete for an account row, in the same "⋯" menu as every other list.
 * @example `<AccountRowActions accountName="Cash" onEdit={edit} onClose={close} deletePending={false} />`
 */
export function AccountRowActions({
  accountName,
  onEdit,
  onClose,
  onDelete,
  deletePending,
}: AccountRowActionsProps) {
  const { t } = useTranslation()
  return (
    <RowActions
      label={accountName}
      onEdit={onEdit}
      onDelete={onDelete}
      deleteDisabled={deletePending}
      extra={[{ label: t('accounts.close'), icon: Archive, onSelect: onClose }]}
    />
  )
}
