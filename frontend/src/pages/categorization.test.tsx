import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import CategorizationPage from '@/pages/categorization'
import { SidebarNavLink } from '@/components/sidebar-nav-link'
import { isCategorizationBusy } from '@/hooks/use-categorization-summary'
import { navItems } from '@/lib/nav-items'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({
  categorization: { summary: vi.fn(), run: vi.fn(), review: vi.fn(), scoreboard: vi.fn() },
  categories: { list: vi.fn() },
  categoryGroups: { list: vi.fn() },
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
const workspace = vi.hoisted(() => ({ canWrite: true, canManage: true }))

vi.mock('@/lib/api', () => api)
vi.mock('sonner', () => ({ toast }))
vi.mock('@/contexts/workspace-context', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/workspace-context')>()),
  useWorkspace: () => workspace,
}))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'BRL' } } }),
}))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

function summary(overrides: Record<string, unknown> = {}) {
  return {
    pending_groups: 3,
    pending_transactions: 12,
    applied_last_7d: 47,
    last_run: {
      id: 'run-1',
      status: 'completed',
      finished_at: new Date(Date.now() - 2 * 3600_000).toISOString(),
      stats: { applied: 40, memory: 7, suggested: 5 },
    },
    ai_available: true,
    month_cost_usd: '0.003',
    monthly_cap_usd: '1.00',
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  workspace.canWrite = true
  workspace.canManage = true
  api.categorization.summary.mockResolvedValue(summary())
  api.categorization.review.mockResolvedValue({ groups: [], total: 0, new_category_proposals: [] })
  api.categorization.scoreboard.mockResolvedValue({ total: 120, decided: 90, to_review: 30, waiting: 0 })
  api.categories.list.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
})

describe('CategorizationPage', () => {
  it('opens on the review tab and shows the last run line', async () => {
    renderWithProviders(<CategorizationPage />, { route: '/categorization' })

    expect(await screen.findByTestId('categorization-review')).toBeInTheDocument()
    expect(await screen.findByText(/12 to review · last run .* applied 47/)).toBeInTheDocument()
    expect(screen.queryByText(t('categorization.aiOff'))).not.toBeInTheDocument()
  })

  it('shows All reviewed, not the run suggestions, when nothing is pending now', async () => {
    api.categorization.summary.mockResolvedValue(
      summary({
        pending_groups: 0,
        pending_transactions: 0,
        last_run: {
          id: 'r', status: 'completed', stats: { applied: 400, memory: 35, suggested: 2660 },
          finished_at: new Date(Date.now() - 3 * 3600_000).toISOString(),
        },
      }),
    )
    renderWithProviders(<CategorizationPage />, { route: '/categorization' })
    const line = await screen.findByTestId('categorization-status')
    expect(line).toHaveTextContent(/All reviewed · last run .* applied 435/)
    expect(line).not.toHaveTextContent('2660')
  })

  it('uses the current pending count, not the last run suggestions', async () => {
    api.categorization.summary.mockResolvedValue(
      summary({ pending_transactions: 12, last_run: { ...summary().last_run, stats: { applied: 1, suggested: 2660 } } }),
    )
    renderWithProviders(<CategorizationPage />, { route: '/categorization' })
    const line = await screen.findByTestId('categorization-status')
    expect(line).toHaveTextContent(/12 to review/)
    expect(line).toHaveTextContent(/applied 1$/)
    expect(line).not.toHaveTextContent('2660')
  })

  it('switches tabs through ?tab=', async () => {
    const { user } = renderWithProviders(<CategorizationPage />, { route: '/categorization' })
    await user.click(await screen.findByTestId('categorization-tab-applied'))
    expect(screen.getByTestId('categorization-applied')).toBeInTheDocument()
  })

  it('opens the requested tab', async () => {
    renderWithProviders(<CategorizationPage />, { route: '/categorization?tab=organize' })
    expect(await screen.findByTestId('categorization-organize')).toBeInTheDocument()
  })

  it('shows the scoreboard next to the status line on every tab', async () => {
    renderWithProviders(<CategorizationPage />, { route: '/categorization?tab=organize' })
    expect(await screen.findByTestId('categorization-scoreboard')).toHaveTextContent(
      '120 transactions · 75% decided · 25% to review',
    )
  })

  it('shows the AI-off strip when no Claude connection is available', async () => {
    api.categorization.summary.mockResolvedValue(summary({ ai_available: false }))
    renderWithProviders(<CategorizationPage />, { route: '/categorization' })
    expect(await screen.findByText(t('categorization.aiOff'))).toBeInTheDocument()
    expect(screen.getByRole('link', { name: t('categorization.configure') })).toHaveAttribute(
      'href',
      '/workspace/settings#categorization',
    )
  })

  it('starts a backlog run and confirms with a toast', async () => {
    api.categorization.run.mockResolvedValue({ id: 'run-2', status: 'queued' })
    const { user } = renderWithProviders(<CategorizationPage />, { route: '/categorization' })
    await user.click(await screen.findByRole('button', { name: t('categorization.runNow') }))
    await waitFor(() => expect(api.categorization.run).toHaveBeenCalledWith({ scope: 'backlog' }))
    expect(toast.success).toHaveBeenCalledWith(t('categorization.runStarted'))
  })

  it('shows the API error when the queue is down (503)', async () => {
    api.categorization.run.mockRejectedValue({
      response: { status: 503, data: { detail: 'Fila indisponível' } },
    })
    const { user } = renderWithProviders(<CategorizationPage />, { route: '/categorization' })
    await user.click(await screen.findByRole('button', { name: t('categorization.runNow') }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Fila indisponível'))
  })

  it('shows Categorizando while a run is busy and hides the run button for read-only roles', async () => {
    api.categorization.summary.mockResolvedValue(
      summary({ last_run: { id: 'r', status: 'running', finished_at: null, stats: {} } }),
    )
    renderWithProviders(<CategorizationPage />, { route: '/categorization' })
    const button = await screen.findByRole('button', { name: t('categorization.running') })
    expect(button).toBeDisabled()
  })

  it('shows the running status line to read-only roles, ahead of the finished run', async () => {
    workspace.canWrite = false
    api.categorization.summary.mockResolvedValue(
      summary({
        last_run: {
          id: 'r', status: 'running', stats: {},
          finished_at: new Date(Date.now() - 3600_000).toISOString(),
        },
      }),
    )
    renderWithProviders(<CategorizationPage />, { route: '/categorization' })
    expect(await screen.findByText(t('categorization.running'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('categorization.running') })).not.toBeInTheDocument()
  })

  it('hides the run button without write access', async () => {
    workspace.canWrite = false
    renderWithProviders(<CategorizationPage />, { route: '/categorization' })
    await screen.findByTestId('categorization-review')
    expect(screen.queryByRole('button', { name: t('categorization.runNow') })).not.toBeInTheDocument()
  })
})

describe('SidebarNavLink', () => {
  const item = navItems.find((i) => i.type === 'link' && i.key === 'categorization')
  if (!item || item.type !== 'link') throw new Error('categorization nav item missing')
  const renderLink = (collapsed = false) =>
    renderWithProviders(
      <SidebarNavLink item={item} isActive={false} collapsed={collapsed} onNavigate={() => {}} />,
    )

  it('is wired to the categorization nav item', () => {
    expect(item).toMatchObject({ module: 'categories', badgeQueryKey: ['categorization', 'summary'] })
  })

  it('shows the pending count and announces it in the link name', async () => {
    renderLink()
    expect(await screen.findByTestId('nav-badge')).toHaveTextContent('3')
    expect(screen.getByRole('link', { name: /3 to review/ })).toBeInTheDocument()
  })

  it('announces the count when the sidebar is collapsed too', async () => {
    renderLink(true)
    expect(await screen.findByRole('link', { name: /3 to review/ })).toBeInTheDocument()
    expect(screen.getByTestId('nav-badge-dot')).toBeInTheDocument()
  })

  it('does not read the summary for an item without a badge', async () => {
    const plain = navItems.find((i) => i.type === 'link' && !i.badgeQueryKey)
    if (!plain || plain.type !== 'link') throw new Error('plain nav item missing')
    renderWithProviders(
      <SidebarNavLink item={plain} isActive={false} collapsed={false} onNavigate={() => {}} />,
    )
    await screen.findByRole('link')
    expect(api.categorization.summary).not.toHaveBeenCalled()
  })

  it('reads the summary for the item with a badge', async () => {
    renderLink()
    await waitFor(() => expect(api.categorization.summary).toHaveBeenCalledTimes(1))
  })

  it('has no badge and a plain name at zero', async () => {
    api.categorization.summary.mockResolvedValue(summary({ pending_groups: 0 }))
    renderLink()
    await waitFor(() => expect(api.categorization.summary).toHaveBeenCalled())
    expect(screen.queryByTestId('nav-badge')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: t('nav.categorization') })).toBeInTheDocument()
  })
})

describe('useCategorizationSummary polling', () => {
  it('polls only while a run is busy', () => {
    expect(isCategorizationBusy(summary({ last_run: { id: 'r', status: 'queued', finished_at: null, stats: {} } }) as never)).toBe(true)
    expect(isCategorizationBusy(summary() as never)).toBe(false)
    expect(isCategorizationBusy(undefined)).toBe(false)
  })
})
