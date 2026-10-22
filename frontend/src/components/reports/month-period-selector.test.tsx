import { describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

import { MonthPeriodSelector } from '@/components/reports/period-selector'
import { renderWithProviders, t } from '@/test/utils'

const previous = () => screen.getByRole('button', { name: t('common.previousMonth') })
const next = () => screen.getByRole('button', { name: t('common.nextMonth') })

describe('MonthPeriodSelector', () => {
  it('shows the selected month in the same frame as the Reports period', () => {
    renderWithProviders(<MonthPeriodSelector value="2026-06" onChange={vi.fn()} locale="en-US" />)

    const group = screen.getByRole('group', { name: t('reports.explore.periodGroup') })
    expect(within(group).getByTestId('period-menu')).toHaveAttribute('title', 'June 2026')
    expect(within(group).getByTestId('period-menu')).toHaveTextContent('June 2026')
  })

  it('steps back and forward a month', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(<MonthPeriodSelector value="2026-06" onChange={onChange} locale="en-US" />)

    await user.click(previous())
    expect(onChange).toHaveBeenLastCalledWith('2026-05')
    await user.click(next())
    expect(onChange).toHaveBeenLastCalledWith('2026-07')
  })

  it('crosses the year boundary both ways', async () => {
    const onChange = vi.fn()
    const { user, rerender } = renderWithProviders(
      <MonthPeriodSelector value="2026-01" onChange={onChange} locale="en-US" />,
    )
    await user.click(previous())
    expect(onChange).toHaveBeenLastCalledWith('2025-12')

    rerender(<MonthPeriodSelector value="2026-12" onChange={onChange} locale="en-US" />)
    await user.click(next())
    expect(onChange).toHaveBeenLastCalledWith('2027-01')
  })

  it('picks a month from the menu', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(<MonthPeriodSelector value="2026-06" onChange={onChange} locale="en-US" />)

    await user.click(screen.getByTestId('period-menu'))
    await user.click(await screen.findByRole('button', { name: /^Sep/ }))
    expect(onChange).toHaveBeenCalledWith('2026-09')
  })

  it('names the month in the given locale', () => {
    renderWithProviders(<MonthPeriodSelector value="2026-05" onChange={vi.fn()} locale="pt-BR" />)

    expect(screen.getByTitle(/Maio de 2026/)).toBeInTheDocument()
  })

  it('holds no state of its own, so the parent stays the source of truth', () => {
    const onChange = vi.fn()
    const { rerender } = renderWithProviders(<MonthPeriodSelector value="2026-06" onChange={onChange} locale="en-US" />)

    rerender(<MonthPeriodSelector value="2026-09" onChange={onChange} locale="en-US" />)
    expect(screen.getByTitle('September 2026')).toBeInTheDocument()
  })
})
