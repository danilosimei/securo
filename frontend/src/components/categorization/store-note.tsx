/** The note the person left on a store ("Note: ..."), with an X to delete
 *  it. Shown in the review row and in "How it got here". */
import { useContext, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { X } from 'lucide-react'

import { categorization as categorizationApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateCategorizationQueries } from '@/lib/invalidate-queries'
import { cn } from '@/lib/utils'
import { WorkspaceContext } from '@/contexts/workspace-context'

interface StoreNoteProps {
  merchantKey: string
  note: string
  className?: string
}

export function StoreNote({ merchantKey, note, className }: StoreNoteProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  // Read directly so it also works outside a workspace (tests).
  const canWrite = useContext(WorkspaceContext)?.canWrite ?? false
  const [deleting, setDeleting] = useState(false)

  const remove = async () => {
    if (deleting) return
    setDeleting(true)
    try {
      await categorizationApi.setNote({ merchant_key: merchantKey, note: null })
      toast.success(t('categorization.note.deleted'))
      invalidateCategorizationQueries(queryClient)
    } catch (err) {
      toast.error(extractApiError(err, t('categorization.note.deleteError')))
    } finally {
      setDeleting(false)
    }
  }

  const text = t('categorization.note.label', { note })
  return (
    <p
      className={cn('flex items-center gap-1 text-xs text-muted-foreground', className)}
      onClick={(e) => e.stopPropagation()}
    >
      <span className="min-w-0 truncate" title={text}>
        {text}
      </span>
      {canWrite && (
        <button
          type="button"
          onClick={remove}
          disabled={deleting}
          aria-label={t('categorization.note.delete')}
          title={t('categorization.note.delete')}
          className="shrink-0 cursor-pointer rounded-sm p-0.5 outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 disabled:opacity-50"
        >
          <X size={12} aria-hidden="true" />
        </button>
      )}
    </p>
  )
}
