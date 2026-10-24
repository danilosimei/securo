import { Layers, Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { PageActions } from '@/components/page-actions'

export type AccountPageActionsProps = {
  canWrite: boolean
  onAddAccount: () => void
  onConnectBank: () => void
  onOpenCollections: () => void
  testId?: string
}

/**
 * The accounts header: "New account" on its own, collections and bank connection in the "⋯" menu.
 * @example `<AccountPageActions canWrite onAddAccount={openForm} onConnectBank={connect} onOpenCollections={openCollections} />`
 */
export function AccountPageActions(props: AccountPageActionsProps) {
  const { t } = useTranslation()
  return (
    <PageActions
      testId={props.testId}
      primary={
        props.canWrite ? (
          <Button onClick={props.onAddAccount}>
            <Plus />
            {t('accounts.addManual')}
          </Button>
        ) : undefined
      }
      items={[
        { label: t('collections.title'), icon: Layers, onSelect: props.onOpenCollections },
        ...(props.canWrite ? [{ label: t('accounts.connectBank'), icon: Plus, onSelect: props.onConnectBank }] : []),
      ]}
    />
  )
}
