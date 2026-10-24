/** "Vai para o patrimônio": under an open group, what its transfer-like
 *  categories (e.g. the purchase of a home) moved in the period. That money
 *  is not spending (it is still the person's, in another form), so it stays
 *  out of the group's total; the last line adds both up as the whole project. */
import { useTranslation } from 'react-i18next'
import { Landmark } from 'lucide-react'
import type { BreakdownAssetRow } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { CategoryGlyph } from '@/components/reports/common/category-glyph'

interface ToAssetsProps {
  rows: BreakdownAssetRow[]
  /** The group's spending in the period (the list's total). */
  spending: number
  currency: string
  locale: string
  iconOf: (id: string) => string | null
  color: string
}

export function ToAssets({ rows, spending, currency, locale, iconOf, color }: ToAssetsProps) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  if (rows.length === 0) return null
  const moved = rows.reduce((sum, row) => sum + Number(row.total), 0)
  const money = (value: number) => mask(formatCurrency(value, currency, locale))
  return (
    <section className="rounded-xl border border-border bg-card shadow-sm" data-testid="explore-to-assets">
      <header className="flex items-center gap-2 border-b border-border px-3 py-2.5 sm:px-4">
        <Landmark size={14} aria-hidden="true" className="text-muted-foreground" />
        <h3 className="text-sm font-semibold text-foreground">{t('reports.explore.assets.title')}</h3>
        <span className="truncate text-xs text-muted-foreground">{t('reports.explore.assets.hint')}</span>
      </header>
      <ul className="divide-y divide-border text-sm">
        {rows.map((row) => (
          <li key={row.id} className="flex items-center justify-between gap-3 px-3 py-2.5 sm:px-4" data-testid="explore-to-assets-row">
            <span className="flex min-w-0 items-center gap-2">
              <CategoryGlyph icon={iconOf(row.id)} color={color} />
              <span className="truncate font-medium text-foreground">{row.label}</span>
            </span>
            <span className="shrink-0 tabular-nums">{money(Number(row.total))}</span>
          </li>
        ))}
        <li className="flex items-center justify-between gap-3 bg-muted/40 px-3 py-2.5 sm:px-4" data-testid="explore-project-total">
          <span className="min-w-0 truncate">
            <span className="font-semibold text-foreground">{t('reports.explore.assets.project')}</span>
            <span className="ml-1.5 text-xs text-muted-foreground">{t('reports.explore.assets.projectHint')}</span>
          </span>
          <span className="shrink-0 font-semibold tabular-nums text-foreground">{money(spending + moved)}</span>
        </li>
      </ul>
    </section>
  )
}
