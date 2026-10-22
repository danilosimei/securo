import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import BudgetsPage from '@/pages/budgets'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({
  budgets: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  categories: { list: vi.fn(), listIncludingHidden: vi.fn() },
  categoryGroups: { list: vi.fn() },
}))

vi.mock('@/lib/api', () => api)

vi.mock('@/contexts/workspace-context', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/workspace-context')>()),
  useWorkspace: () => ({ canWrite: true, canManage: true }),
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 9, 21, 12))
  vi.clearAllMocks()
  api.budgets.list.mockResolvedValue([])
  api.categories.list.mockResolvedValue([])
  api.categories.listIncludingHidden.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
})

describe('Budgets month', () => {
  it('sits in the header where Reports puts its period, with the same control', async () => {
    const { user } = renderWithProviders(<BudgetsPage />)
    await waitFor(() => expect(api.budgets.list).toHaveBeenCalledWith('2026-10-01'))

    const slot = screen.getByTestId('page-period')
    expect(slot).toHaveClass('w-full', 'sm:ml-auto', 'sm:w-auto')
    // The period sits beside the page's primary action, both at the right of the title.
    const header = slot.parentElement!.parentElement!
    expect(header).toHaveClass('flex-col', 'lg:flex-row', 'lg:justify-between')
    expect(header).toContainElement(screen.getByRole('heading', { level: 1 }))
    expect(slot.parentElement).toContainElement(screen.getByRole('button', { name: t('budgets.add') }))
    const group = within(slot).getByRole('group', { name: t('reports.explore.periodGroup') })

    await user.click(within(group).getByRole('button', { name: t('common.previousMonth') }))
    await waitFor(() => expect(api.budgets.list).toHaveBeenCalledWith('2026-09-01'))
    expect(within(group).getByTestId('period-menu')).toHaveTextContent('September 2026')
  })
})
