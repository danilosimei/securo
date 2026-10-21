import { describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/react'

import { CashflowSankey } from '@/components/reports/CashflowSankey'
import { renderWithProviders } from '@/test/utils'
import ptBR from '@/locales/pt-BR.json'

const item = (key: string, group: string, value: number) => ({
  key,
  label: key,
  value,
  color: '#000000',
  group,
})

function renderMap(composition: ReturnType<typeof item>[]) {
  return renderWithProviders(<CashflowSankey composition={composition} currency="BRL" locale="pt-BR" />)
}

describe('CashflowSankey without an investments lane', () => {
  it('renders income, spending groups and the leftover node', () => {
    renderMap([item('Salário', 'income', 10000), item('Moradia', 'expenses', 4000), item('Lazer', 'expenses', 1000)])

    const table = screen.getByRole('table', { hidden: true })
    for (const name of ['Salário', 'Moradia', 'Lazer', 'Surplus']) {
      expect(within(table).getByText(name)).toBeInTheDocument()
    }
    expect(within(table).queryByText('Investments')).not.toBeInTheDocument()
  })

  it('names the shortfall node when spending is above income', () => {
    renderMap([item('Salário', 'income', 1000), item('Moradia', 'expenses', 4000)])

    const table = screen.getByRole('table', { hidden: true })
    expect(within(table).getByText('Deficit')).toBeInTheDocument()
    expect(within(table).queryByText('Surplus')).not.toBeInTheDocument()
  })

  it('shows the empty state when nothing came', () => {
    renderMap([])

    expect(screen.getByText('No data available for this period')).toBeInTheDocument()
  })
})

describe('pt-BR names of the leftover and shortfall nodes', () => {
  it('say Sobrou and Faltou', () => {
    expect(ptBR.reports.surplus).toBe('Sobrou')
    expect(ptBR.reports.deficit).toBe('Faltou')
  })
})
