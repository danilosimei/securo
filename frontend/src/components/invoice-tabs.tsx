import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { Segmented } from '@/components/invoice-ui'

export const INVOICE_TABS = ['invoices', 'schedules', 'products'] as const
export type InvoiceTab = (typeof INVOICE_TABS)[number]

/**
 * The three parts of Invoices: the invoices, the recurring ones and the
 * products. The tab lives in `?tab=`; the first is the default and stays out
 * of the URL. Switching drops the other params (year, state, direction), which
 * only mean something inside one tab.
 * @example `<InvoiceTabs tab="schedules" />`
 */
export function InvoiceTabs({ tab, trailing }: { tab: InvoiceTab; trailing?: React.ReactNode }) {
  const { t } = useTranslation()
  const [, setSearchParams] = useSearchParams()
  const labels: Record<InvoiceTab, string> = {
    invoices: t('invoices.title'),
    schedules: t('invoices.schedules.title'),
    products: t('invoices.products.title'),
  }
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <Segmented<InvoiceTab>
        value={tab}
        onChange={(next) => setSearchParams(next === 'invoices' ? {} : { tab: next }, { replace: true })}
        testIdPrefix="invoice-tab"
        options={INVOICE_TABS.map((value) => ({ value, label: labels[value] }))}
      />
      {trailing}
    </div>
  )
}
