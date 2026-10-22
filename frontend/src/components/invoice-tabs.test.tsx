import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import { useLocation } from 'react-router-dom'

import { InvoiceTabs } from '@/components/invoice-tabs'
import { YearPeriodSelector } from '@/components/reports/period-selector'
import { renderWithProviders, t } from '@/test/utils'

function Where() {
  return <p data-testid="where">{useLocation().search}</p>
}

describe('InvoiceTabs', () => {
  it('switches tab through ?tab= and drops the params of the tab it leaves', async () => {
    const { user } = renderWithProviders(<><InvoiceTabs tab="invoices" /><Where /></>, { route: '/?year=2025&state=open' })
    await user.click(screen.getByRole('button', { name: t('invoices.schedules.title') }))
    expect(screen.getByTestId('where')).toHaveTextContent('?tab=schedules')
    await user.click(screen.getByRole('button', { name: t('invoices.title') }))
    expect(screen.getByTestId('where')).toBeEmptyDOMElement()
  })
})

describe('YearPeriodSelector', () => {
  it('offers every year plus the years that have something, and shows the chosen one', async () => {
    const seen: (number | null)[] = []
    const { user } = renderWithProviders(
      <YearPeriodSelector value={2026} years={[2025]} onChange={(y) => seen.push(y)} yearLabel={(y) => `Y${y}`} allLabel="All" />,
    )
    expect(screen.getByTestId('period-menu')).toHaveTextContent('Y2026')
    await user.click(screen.getByTestId('period-menu'))
    expect(screen.getByRole('menuitemradio', { name: 'Y2025' })).toBeInTheDocument()
    await user.click(screen.getByRole('menuitemradio', { name: 'All' }))
    expect(seen).toEqual([null])
  })
})
