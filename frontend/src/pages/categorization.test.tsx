import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import CategorizationPage from '@/pages/categorization'
import { NavBadge } from '@/components/nav-badge'
import { navItems } from '@/lib/nav-items'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({
  categorization: { summary: vi.fn(), run: vi.fn() },
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
const workspace = vi.hoisted(() => ({ canWrite: true, canManage: true }))

vi.mock('@/lib/api', () => ({ categorization: api.categorization }))
vi.mock('sonner', () => ({ toast }))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => workspace,
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
})

describe('CategorizationPage', () => {
  it('opens on the review tab and shows the last run line', async () => {
    renderWithProviders(<CategorizationPage />, { route: '/categorization' })

    expect(await screen.findByTestId('categorization-review')).toBeInTheDocument()
    expect(await screen.findByText(/47 applied · 5 to review/)).toBeInTheDocument()
    expect(screen.queryByText(t('categorization.aiOff'))).not.toBeInTheDocument()
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

  it('shows the AI-off strip when no Claude connection is available', async () => {
    api.categorization.summary.mockResolvedValue(summary({ ai_available: false }))
    renderWithProviders(<CategorizationPage />, { route: '/categorization' })
    expect(await screen.findByText(t('categorization.aiOff'))).toBeInTheDocument()
    expect(screen.getByRole('link', { name: t('categorization.configure') })).toHaveAttribute(
      'href',
      '/workspace/settings',
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

  it('hides the run button without write access', async () => {
    workspace.canWrite = false
    renderWithProviders(<CategorizationPage />, { route: '/categorization' })
    await screen.findByTestId('categorization-review')
    expect(screen.queryByRole('button', { name: t('categorization.runNow') })).not.toBeInTheDocument()
  })
})

describe('NavBadge', () => {
  it('is wired to the categorization nav item', () => {
    const item = navItems.find((i) => i.type === 'link' && i.key === 'categorization')
    expect(item).toMatchObject({ module: 'categories', badgeQueryKey: ['categorization', 'summary'] })
  })

  it('shows the pending group count', async () => {
    renderWithProviders(<NavBadge queryKey={['categorization', 'summary']} />)
    expect(await screen.findByTestId('nav-badge')).toHaveTextContent('3')
  })

  it('is hidden at zero', async () => {
    api.categorization.summary.mockResolvedValue(summary({ pending_groups: 0 }))
    renderWithProviders(<NavBadge queryKey={['categorization', 'summary']} />)
    await waitFor(() => expect(api.categorization.summary).toHaveBeenCalled())
    expect(screen.queryByTestId('nav-badge')).not.toBeInTheDocument()
  })
})
