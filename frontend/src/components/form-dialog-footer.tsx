import { Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { DialogFooter } from '@/components/ui/dialog'
import { cn } from '@/lib/utils'

/**
 * Footer of a create/edit dialog: Cancel and Save at the right and, when
 * editing, Delete alone at the left. Pass `onDelete` only for an existing
 * item, so "new" has no Delete. Delete asks to confirm in its own dialog.
 * @example `<FormDialogFooter onCancel={close} onDelete={editing ? askDelete : undefined} saveDisabled={saving} />`
 */
export function FormDialogFooter({
  onCancel,
  onSave,
  onDelete,
  saveDisabled,
  saveLabel,
  deleteDisabled,
  className,
}: {
  onCancel: () => void
  /** Leave out inside a `<form>`: Save then submits it. */
  onSave?: () => void
  onDelete?: () => void
  saveDisabled?: boolean
  saveLabel?: string
  deleteDisabled?: boolean
  className?: string
}) {
  const { t } = useTranslation()
  return (
    <DialogFooter className={cn(onDelete && 'sm:justify-between', className)}>
      {onDelete && (
        <Button type="button" variant="destructive" onClick={onDelete} disabled={deleteDisabled}>
          <Trash2 size={16} />
          {t('common.delete')}
        </Button>
      )}
      <div className="flex flex-col-reverse gap-2 sm:flex-row">
        <Button type="button" variant="outline" onClick={onCancel}>
          {t('common.cancel')}
        </Button>
        <Button type={onSave ? 'button' : 'submit'} onClick={onSave} disabled={saveDisabled}>
          {saveLabel ?? t('common.save')}
        </Button>
      </div>
    </DialogFooter>
  )
}
