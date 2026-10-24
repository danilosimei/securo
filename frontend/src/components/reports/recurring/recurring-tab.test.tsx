import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { useLocation } from 'react-router-dom'

import { RecurringTab } from '@/components/reports/recurring/recurring-tab'
import { renderWithProviders, t } from '@/test/utils'
import { GROUPS, overviewFixture } from '@/test/reports-fixtures'
import type { RecurringRow } from '@/lib/api'

const api = vi.hoisted(() => ({
  reports: { recurring: vi.fn(), overview: vi.fn(), endRecurring: vi.fn(), undoEndRecurring: vi.fn() },
  categoryGroups: { list: vi.fn() },
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
const workspace = vi.hoisted(() => ({ canWrite: true }))

vi.mock('@/lib/api', () => ({ reports: api.reports, categoryGroups: api.categoryGroups }))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'BRL' } } }),
}))
vi.mock('sonner', () => ({ toast }))
vi.mock('@/contexts/workspace-context', async (importOriginal) => {
  const { createContext } = await import('react')
  return {
    ...(await importOriginal<typeof import('@/contexts/workspace-context')>()),
    WorkspaceContext: createContext(workspace),
  }
})
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

const rec = (
  key: string,
  label: string,
  [category, categoryId]: [string, string | null],
  month: number,
  status: RecurringRow['status'],
  last = '2026-09-05',
): RecurringRow => ({
  merchant_key: key,
  label,
  category,
  category_id: categoryId,
  amount_month: month.toFixed(2),
  amount_year: (month * 12).toFixed(2),
  status,
  last_date: last,
  months_seen: 12,
})

const ROWS = [
  rec('NETFLIX', 'Netflix', ['Assinaturas', 'c-subs'], 55.9, 'up', '2026-10-02'),
  rec('SON PET VIDA', 'Son Pet Vida', ['Pets', 'c-pets'], 786, 'steady'),
  rec('STELLANTIS', 'Stellantis', ['Financiamento', 'c-loan'], 4265, 'steady'),
  { ...rec('GYM', 'Academia', ['Saúde', 'c-health'], 120, 'stopped', '2026-05-10'), ended_on: '2026-05-20' },
  rec('PIX JOAO', 'Pix João', ['Sem categoria', null], 300, 'new'),
]

function Probe() {
  const location = useLocation()
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
}

beforeEach(() => {
  vi.clearAllMocks()
  workspace.canWrite = true
  api.categoryGroups.list.mockResolvedValue(GROUPS)
  api.reports.recurring.mockResolvedValue(ROWS)
  // R$ 13,800 a month of spending: the active fixed costs are 39% of it.
  api.reports.overview.mockResolvedValue(
    overviewFixture({ kpis: { spending: '13800.00', income: '20000.00', left: '6200.00', left_share: 0.31, fixed_share: 0.4 } }),
  )
})

const names = () => screen.getAllByTestId('recurring-row').map((r) => within(r).getAllByText(/./)[0].textContent)

describe('RecurringTab', () => {
  it('opens with the monthly and yearly cost of the fixed charges and their share of spending', async () => {
    renderWithProviders(<RecurringTab />, { route: '/reports?tab=recurring&period=last_12m' })

    await waitFor(() =>
      expect(screen.getByTestId('report-headline')).toHaveTextContent(
        'You pay R$5,407 a month in fixed costs, R$64.9K a year: 39% of what you spend.',
      ),
    )
    expect(screen.getByText('Active: 4 · New: 1 · Went up: 1 · Ended: 1')).toBeInTheDocument()
  })

  it('lists the active ones, most expensive per year first, and the ended ones apart, at the end', async () => {
    renderWithProviders(<RecurringTab />)

    await screen.findAllByTestId('recurring-row')
    expect(names()).toEqual(['Stellantis', 'Son Pet Vida', 'Pix João', 'Netflix'])
    const first = screen.getAllByTestId('recurring-row')[0]
    expect(within(first).getByTestId('status-steady')).toHaveTextContent(t('reports.recurring.status.steady'))
    expect(screen.getByTestId('status-new')).toBeInTheDocument()
    expect(screen.getByTestId('status-up')).toBeInTheDocument()
    expect(first).toHaveTextContent('R$51.2K/year')
    const ended = screen.getByTestId('recurring-ended')
    expect(ended).toHaveTextContent(t('reports.recurring.endedTitle', { count: 1 }))
    const row = within(ended).getByTestId('recurring-ended-row')
    expect(row).toHaveTextContent('Academia')
    expect(row).toHaveTextContent(t('reports.recurring.lastCharge', { date: 'May 10, 26' }))
  })

  it('marking one as ended moves it to the ended ones now, with an undo', async () => {
    api.reports.endRecurring.mockResolvedValue({ merchant_key: 'STELLANTIS', ended_on: '2026-10-22' })
    api.reports.undoEndRecurring.mockResolvedValue({ merchant_key: 'STELLANTIS', ended_on: null })
    const { user } = renderWithProviders(<RecurringTab />)
    await screen.findAllByTestId('recurring-row')

    await user.click(screen.getAllByTestId('recurring-menu')[0])
    await user.click(await screen.findByTestId('recurring-end'))

    await waitFor(() => expect(api.reports.endRecurring).toHaveBeenCalledWith('STELLANTIS'))
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    const [message, options] = toast.success.mock.calls[0]
    expect(message).toBe(t('reports.recurring.endedToast'))
    options.action.onClick()
    await waitFor(() => expect(api.reports.undoEndRecurring).toHaveBeenCalledWith('STELLANTIS'))
    // The list is asked again after each change.
    await waitFor(() => expect(api.reports.recurring.mock.calls.length).toBeGreaterThanOrEqual(3))
  })

  it('an ended one marked by the person can be undone from the list', async () => {
    api.reports.undoEndRecurring.mockResolvedValue({ merchant_key: 'GYM', ended_on: null })
    const { user } = renderWithProviders(<RecurringTab />)
    await user.click(await screen.findByTestId('recurring-undo'))
    await waitFor(() => expect(api.reports.undoEndRecurring).toHaveBeenCalledWith('GYM'))
  })

  it('who can only read sees no menu and no undo', async () => {
    workspace.canWrite = false
    renderWithProviders(<RecurringTab />)
    await screen.findAllByTestId('recurring-row')
    expect(screen.queryByTestId('recurring-menu')).not.toBeInTheDocument()
    expect(screen.queryByTestId('recurring-undo')).not.toBeInTheDocument()
  })

  it('sorts by the latest charge or by name, and filters by status', async () => {
    const { user } = renderWithProviders(<RecurringTab />)
    await screen.findAllByTestId('recurring-row')

    await user.click(screen.getByTestId('recurring-sort-recent'))
    expect(names()[0]).toBe('Netflix')
    await user.click(screen.getByTestId('recurring-sort-name'))
    expect(names()).toEqual(['Netflix', 'Pix João', 'Son Pet Vida', 'Stellantis'])

    await user.click(screen.getByTestId('recurring-filter-new'))
    expect(names()).toEqual(['Pix João'])
  })

  it('a charge opens its store in Explore, under its category and group', async () => {
    const { user } = renderWithProviders(
      <>
        <RecurringTab />
        <Probe />
      </>,
      { route: '/reports?tab=recurring&period=last_6m' },
    )
    await screen.findAllByTestId('recurring-row')
    await waitFor(() => expect(api.categoryGroups.list).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: /^Son Pet Vida/ }))
    expect(screen.getByTestId('location')).toHaveTextContent(
      '/reports?tab=explore&period=last_12m&group=g-life&category=c-pets&merchant=SON+PET+VIDA',
    )
  })

  it('a charge opens its category by id, even when another category has the same name', async () => {
    api.reports.recurring.mockResolvedValue([rec('BOOKS', 'Livraria', ['Presentes', 'c-books'], 90, 'steady')])
    const { user } = renderWithProviders(
      <>
        <RecurringTab />
        <Probe />
      </>,
    )
    await screen.findAllByTestId('recurring-row')
    await waitFor(() => expect(api.categoryGroups.list).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: /^Livraria/ }))
    expect(screen.getByTestId('location')).toHaveTextContent('group=g-edu&category=c-books&merchant=BOOKS')
  })

  it('a charge without category opens the uncategorized store', async () => {
    const { user } = renderWithProviders(
      <>
        <RecurringTab />
        <Probe />
      </>,
    )
    await user.click(await screen.findByRole('button', { name: /^Pix João/ }))
    expect(screen.getByTestId('location')).toHaveTextContent('category=none&merchant=PIX+JOAO')
  })

  it('retries after an error', async () => {
    api.reports.recurring.mockRejectedValueOnce(new Error('boom'))
    const { user } = renderWithProviders(<RecurringTab />)
    await user.click(await screen.findByRole('button', { name: t('reports.common.retry') }))
    expect(await screen.findAllByTestId('recurring-row')).toHaveLength(4)
  })

  it('shows the empty state', async () => {
    api.reports.recurring.mockResolvedValue([])
    renderWithProviders(<RecurringTab />)
    expect(await screen.findByText(t('reports.recurring.empty'))).toBeInTheDocument()
  })
})
