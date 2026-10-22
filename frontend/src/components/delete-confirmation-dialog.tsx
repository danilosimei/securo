import { useTranslation } from 'react-i18next'
import { Trash2, type LucideIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

type DeleteConfirmationDialogProps = {
  open: boolean
  title: string
  description: string
  isPending: boolean
  onClose: () => void
  onCancel?: () => void
  onConfirm: () => void
  /** The confirm button's text; "Delete" by default. */
  confirmLabel?: string
  confirmIcon?: LucideIcon
}

export function DeleteConfirmationDialog({
  open,
  title,
  description,
  isPending,
  onClose,
  onCancel = onClose,
  onConfirm,
  confirmLabel,
  confirmIcon: ConfirmIcon = Trash2,
}: DeleteConfirmationDialogProps) {
  const { t } = useTranslation()

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen && !isPending) onClose()
      }}
    >
      <DialogContent size="sm" showCloseButton={!isPending}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={isPending}>
            {t('common.cancel')}
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={isPending}>
            <ConfirmIcon size={14} className="mr-1" />
            {confirmLabel ?? t('common.delete')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
