import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { DateRangeFields } from '@/components/date-range-fields'
import { renderWithProviders, t } from '@/test/utils'

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'pt-BR',
  useDateLocale: () => 'pt-BR',
}))

function Harness({
  initialFrom = '',
  initialTo = '',
  onSubmit = () => {},
  onValidity = () => {},
}: {
  initialFrom?: string
  initialTo?: string
  onSubmit?: () => void
  onValidity?: (v: boolean) => void
}) {
  const [from, setFrom] = useState(initialFrom)
  const [to, setTo] = useState(initialTo)
  return (
    <>
      <DateRangeFields
        from={from}
        to={to}
        onChange={(f, t2) => {
          setFrom(f)
          setTo(t2)
        }}
        onValidityChange={onValidity}
        onSubmit={onSubmit}
      />
      <output data-testid="iso">{`${from}|${to}`}</output>
      <button onClick={() => setFrom('2026-05-10')}>pick</button>
    </>
  )
}

describe('DateRangeFields', () => {
  it('turns typed text into ISO dates', async () => {
    const { user } = renderWithProviders(<Harness />)
    await user.type(screen.getByLabelText(t('transactions.filtersBar.fromLabel')), '1/2/26')
    await user.type(screen.getByLabelText(t('transactions.filtersBar.toLabel')), '15/03/2026')
    expect(screen.getByTestId('iso')).toHaveTextContent('2026-02-01|2026-03-15')
  })

  it('follows a change made from outside (calendar) and shows it formatted', async () => {
    const { user } = renderWithProviders(<Harness />)
    await user.click(screen.getByText('pick'))
    expect(screen.getByLabelText(t('transactions.filtersBar.fromLabel'))).toHaveValue('10/05/2026')
  })

  it('flags "To" before "From" inline and reports invalid', async () => {
    const onValidity = vi.fn()
    const { user } = renderWithProviders(<Harness onValidity={onValidity} />)
    await user.type(screen.getByLabelText(t('transactions.filtersBar.fromLabel')), '10/03/2026')
    await user.type(screen.getByLabelText(t('transactions.filtersBar.toLabel')), '01/03/2026')
    expect(screen.getByRole('alert')).toHaveTextContent(t('transactions.filtersBar.rangeOrder'))
    expect(onValidity).toHaveBeenLastCalledWith(false)
  })

  it('flags unparseable text', async () => {
    const { user } = renderWithProviders(<Harness />)
    await user.type(screen.getByLabelText(t('transactions.filtersBar.fromLabel')), '31/02/2026')
    expect(screen.getByRole('alert')).toHaveTextContent(t('transactions.filtersBar.invalidDate'))
  })

  it('allows one side only and submits with Enter', async () => {
    const onSubmit = vi.fn()
    const { user } = renderWithProviders(<Harness onSubmit={onSubmit} />)
    await user.type(screen.getByLabelText(t('transactions.filtersBar.fromLabel')), '01/02/2026{Enter}')
    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('iso')).toHaveTextContent('2026-02-01|')
  })

  it('does not submit with Enter while invalid', async () => {
    const onSubmit = vi.fn()
    const { user } = renderWithProviders(
      <Harness initialFrom="2026-03-10" initialTo="2026-03-01" onSubmit={onSubmit} />,
    )
    await user.type(screen.getByLabelText(t('transactions.filtersBar.toLabel')), '{Enter}')
    expect(onSubmit).not.toHaveBeenCalled()
  })
})
