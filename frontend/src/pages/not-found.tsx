import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'

/** Shown inside the app layout for an address that does not exist. */
export default function NotFoundPage() {
  const { t } = useTranslation()
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-3 py-20 text-center" data-testid="not-found">
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">{t('notFound.title')}</h1>
      <p className="text-sm text-muted-foreground">{t('notFound.description')}</p>
      <Button asChild className="mt-2">
        <Link to="/">{t('notFound.goHome')}</Link>
      </Button>
    </div>
  )
}
