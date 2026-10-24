/** State and save for a page's "create rule" dialog, opened pre-filled from
 *  a transaction or from the correction toast. The page renders
 *  `<RuleDialog key={ruleDialogKey} {...ruleDialogProps} …lists />`. */
import { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { rules as rulesApi } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import type { RuleDialogInitialData } from '@/components/rule-dialog'
import type { Rule } from '@/types'

export function useCreateRuleDialog() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [initialData, setInitialData] = useState<RuleDialogInitialData | undefined>(undefined)

  const openRuleDialog = useCallback((data?: RuleDialogInitialData) => {
    setInitialData(data)
    setOpen(true)
  }, [])
  const close = useCallback(() => {
    setOpen(false)
    setInitialData(undefined)
  }, [])

  const mutation = useMutation({
    mutationFn: (data: Omit<Rule, 'id' | 'user_id'>) => rulesApi.create(data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      close()
      const applied = result.applied_count ?? 0
      if (applied > 0) {
        invalidateFinancialQueries(queryClient)
        queryClient.invalidateQueries({ queryKey: ['payees'] })
        toast.success(t('rules.createdAndApplied', { count: applied }))
      } else {
        toast.success(t('rules.created'))
      }
    },
    onError: (error: unknown) => {
      const err = error as { response?: { status?: number } }
      toast.error(t(err?.response?.status === 409 ? 'rules.duplicateName' : 'common.error'))
    },
  })

  return {
    openRuleDialog,
    isRuleDialogOpen: open,
    /** Remounts the dialog on open so it re-reads `initialData`. */
    ruleDialogKey: open ? 'rule-open' : 'rule-closed',
    ruleDialogProps: {
      open,
      onClose: close,
      rule: null,
      onSave: (data: Partial<Rule>) => mutation.mutate(data as Omit<Rule, 'id' | 'user_id'>),
      loading: mutation.isPending,
      initialData,
    },
  }
}
