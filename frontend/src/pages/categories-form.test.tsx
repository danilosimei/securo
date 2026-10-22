/**
 * The category dialog: how reports read the category, and what belongs in it.
 */
import { createContext } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import CategoriesPage from '@/pages/categories'
import { renderWithProviders } from '@/test/utils'

const api = vi.hoisted(() => ({
  categories: {
    listIncludingHidden: vi.fn(),
    usage: vi.fn(),
    delete: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    ruleUsage: vi.fn(),
  },
  categoryGroups: {
    listIncludingHidden: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
}))

vi.mock('@/lib/api', () => ({
  categories: api.categories,
  categoryGroups: api.categoryGroups,
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
  WorkspaceContext: createContext<{ canWrite: boolean } | undefined>({ canWrite: true }),
}))

function category(overrides = {}) {
  return {
    id: 'food',
    user_id: 'user-1',
    name: 'Food',
    icon: 'circle-help',
    color: '#6366f1',
    group_id: null,
    is_system: true,
    is_hidden: false,
    treat_as_transfer: false,
    is_ignored: false,
    report_kind: 'normal',
    description: null,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.categoryGroups.listIncludingHidden.mockResolvedValue([])
  api.categories.update.mockResolvedValue(category())
  api.categories.create.mockResolvedValue(category())
})

async function openEdit(user: ReturnType<typeof renderWithProviders>['user']) {
  await user.click(await screen.findByTitle('Edit'))
  return screen.findByRole('dialog')
}

describe('category dialog: reports and description', () => {
  it('saves the report kind and the description of an existing category', async () => {
    api.categories.listIncludingHidden.mockResolvedValue([
      category({ report_kind: 'income', description: 'Salary only' }),
    ])
    const { user } = renderWithProviders(<CategoriesPage />)
    await openEdit(user)

    // Shows what is saved, with the help for it.
    const kind = screen.getByLabelText('In reports')
    expect(kind).toHaveValue('income')
    expect(screen.getByText('Money coming in here counts as income.')).toBeInTheDocument()
    expect(screen.getByLabelText('What goes here (optional)')).toHaveValue('Salary only')

    await user.selectOptions(kind, 'refund')
    expect(
      screen.getByText('Credits here reduce spending and do not count as income.'),
    ).toBeInTheDocument()
    const description = screen.getByLabelText('What goes here (optional)')
    await user.clear(description)
    await user.type(description, 'Refunds and cashback')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(api.categories.update).toHaveBeenCalledWith(
        'food',
        expect.objectContaining({ report_kind: 'refund', description: 'Refunds and cashback' }),
      ),
    )
  })

  it('creates a normal category with no description when nothing is typed', async () => {
    api.categories.listIncludingHidden.mockResolvedValue([])
    const { user } = renderWithProviders(<CategoriesPage />)
    await user.click(await screen.findByRole('button', { name: /Add Category/i }))
    await screen.findByRole('dialog')

    expect(screen.getByLabelText('In reports')).toHaveValue('normal')
    expect(screen.getByText('Spending and refunds cancel each other out here.')).toBeInTheDocument()
    await user.type(document.querySelector('input[name="name"]') as HTMLInputElement, 'Games')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(api.categories.create).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'Games', report_kind: 'normal', description: null }),
      ),
    )
  })

  it('limits the description to 300 characters and counts them', async () => {
    api.categories.listIncludingHidden.mockResolvedValue([category()])
    const { user } = renderWithProviders(<CategoriesPage />)
    await openEdit(user)

    const description = screen.getByLabelText('What goes here (optional)')
    expect(description).toHaveAttribute('maxlength', '300')
    expect(screen.getByText('0/300')).toBeInTheDocument()
    await user.click(description)
    await user.paste('x'.repeat(350))
    expect(description).toHaveValue('x'.repeat(300))
    expect(screen.getByText('300/300')).toBeInTheDocument()
  })
})
