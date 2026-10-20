import { useTranslation } from 'react-i18next'

/** Placeholder: Task 18 replaces it with the organizer. */
export function OrganizeTab() {
  const { t } = useTranslation()
  return (
    <div data-testid="categorization-organize" className="rounded-xl border border-border bg-card p-8 text-center text-sm text-muted-foreground">
      {t('categorization.soon')}
    </div>
  )
}
