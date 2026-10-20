import { useTranslation } from 'react-i18next'

/** Placeholder: Task 13 replaces it with the audit list. */
export function AppliedTab() {
  const { t } = useTranslation()
  return (
    <div data-testid="categorization-applied" className="rounded-xl border border-border bg-card p-8 text-center text-sm text-muted-foreground">
      {t('categorization.soon')}
    </div>
  )
}
