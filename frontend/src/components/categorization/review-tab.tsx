import { useTranslation } from 'react-i18next'

/** Placeholder: Task 12 replaces it with the review queue. */
export function ReviewTab() {
  const { t } = useTranslation()
  return (
    <div data-testid="categorization-review" className="rounded-xl border border-border bg-card p-8 text-center text-sm text-muted-foreground">
      {t('categorization.soon')}
    </div>
  )
}
