import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { CategorizationSettingsSection } from '@/components/categorization/categorization-settings-section'
import { renderWithProviders } from '@/test/utils'

const api = vi.hoisted(() => ({
  categorization: { getSettings: vi.fn(), updateSettings: vi.fn() },
  agents: { connections: { list: vi.fn() } },
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), dismiss: vi.fn() }))
const workspace = vi.hoisted(() => ({ canWrite: true, canManage: true }))
const flags = vi.hoisted(() => ({ agentsEnabled: true }))
const summaryHook = vi.hoisted(() => ({
  data: { month_cost_usd: '0.003', monthly_cap_usd: '1.00' } as unknown,
}))

vi.mock('@/lib/api', () => api)
vi.mock('sonner', () => ({ toast }))
vi.mock('@/contexts/workspace-context', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/workspace-context')>()),
  useWorkspace: () => workspace,
}))
vi.mock('@/hooks/use-feature-flags', () => ({ useFeatureFlags: () => ({ ...flags, isLoading: false }) }))
vi.mock('@/hooks/use-categorization-summary', () => ({
  CATEGORIZATION_SUMMARY_KEY: ['categorization', 'summary'],
  useCategorizationSummary: () => ({ data: summaryHook.data, isLoading: false }),
}))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'pt-BR',
  useDateLocale: () => 'pt-BR',
}))

const base = {
  enabled: true,
  auto_apply_threshold: 90,
  monthly_cap_usd: '1.00',
  llm_connection_id: 'conn1',
  owner_user_id: 'u1',
  last_run_at: null,
}
const connection = { id: 'conn1', name: 'Minha Claude', kind: 'anthropic', is_default: true }

describe('CategorizationSettingsSection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    workspace.canManage = true
    flags.agentsEnabled = true
    api.categorization.getSettings.mockResolvedValue({ ...base })
    api.categorization.updateSettings.mockImplementation(async (patch) => ({ ...base, ...patch }))
    api.agents.connections.list.mockResolvedValue([connection])
  })

  it('changing the segmented control PUTs the threshold', async () => {
    const { user } = renderWithProviders(<CategorizationSettingsSection />)
    await user.click(await screen.findByTestId('categorization-threshold-95'))
    await waitFor(() => expect(api.categorization.updateSettings).toHaveBeenCalledWith({ auto_apply_threshold: 95 }))
  })

  it('toggles the switch with a PUT', async () => {
    const { user } = renderWithProviders(<CategorizationSettingsSection />)
    await user.click(await screen.findByRole('switch'))
    await waitFor(() => expect(api.categorization.updateSettings).toHaveBeenCalledWith({ enabled: false }))
  })

  it('shows Personalizado with nothing selected when the threshold is off the presets', async () => {
    api.categorization.getSettings.mockResolvedValue({ ...base, auto_apply_threshold: 85 })
    renderWithProviders(<CategorizationSettingsSection />)
    expect(await screen.findByTestId('categorization-threshold-custom')).toHaveTextContent(/Personalizado \(85\)|Custom \(85\)/)
    expect(screen.getByTestId('categorization-threshold-custom').className).toContain('shadow-sm')
    expect(await screen.findByText(/Personalizado \(85\)|Custom \(85\)/)).toBeInTheDocument()
    for (const v of ['95', '90', '80']) {
      expect(screen.getByTestId(`categorization-threshold-${v}`).className).not.toContain('shadow-sm')
    }
  })

  it('validates the cap before saving', async () => {
    const { user } = renderWithProviders(<CategorizationSettingsSection />)
    const input = await screen.findByLabelText(/cap|teto/i)
    await user.clear(input)
    await user.type(input, '1500')
    await user.tab()
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(api.categorization.updateSettings).not.toHaveBeenCalled()
  })

  it('saves a valid cap on Enter, accepting a comma', async () => {
    const { user } = renderWithProviders(<CategorizationSettingsSection />)
    const input = await screen.findByLabelText(/cap|teto/i)
    await user.clear(input)
    await user.type(input, '2,5{Enter}')
    await waitFor(() => expect(api.categorization.updateSettings).toHaveBeenCalledWith({ monthly_cap_usd: '2.50' }))
  })

  it('shows the spend line with sub-cent precision', async () => {
    renderWithProviders(<CategorizationSettingsSection />)
    expect(await screen.findByText(/0,003/)).toBeInTheDocument()
  })

  it('reverts and toasts when the PUT fails', async () => {
    api.categorization.updateSettings.mockRejectedValue(new Error('403'))
    const { user } = renderWithProviders(<CategorizationSettingsSection />)
    const sw = await screen.findByRole('switch')
    await user.click(sw)
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true'))
  })

  it('disables the whole form without canManage', async () => {
    workspace.canManage = false
    renderWithProviders(<CategorizationSettingsSection />)
    expect(await screen.findByRole('switch')).toBeDisabled()
    expect(screen.getByTestId('categorization-threshold-95')).toBeDisabled()
    expect(screen.getByLabelText(/cap|teto/i)).toBeDisabled()
    expect(screen.queryByRole('link', { name: /organiz/i })).toBeNull()
  })

  it('names the connection in use', async () => {
    renderWithProviders(<CategorizationSettingsSection />)
    expect(await screen.findByTestId('categorization-connection-name')).toHaveTextContent('Minha Claude')
  })

  it('handles the connections endpoint 404 without breaking', async () => {
    api.agents.connections.list.mockRejectedValue({ response: { status: 404 } })
    renderWithProviders(<CategorizationSettingsSection />)
    expect(await screen.findByRole('switch')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByTestId('categorization-connection-name')).toBeNull())
    expect(screen.queryByRole('link', { name: /connection|conex/i })).toBeNull()
  })

  it('does not query connections when agents are off', async () => {
    flags.agentsEnabled = false
    renderWithProviders(<CategorizationSettingsSection />)
    await screen.findByRole('switch')
    expect(api.agents.connections.list).not.toHaveBeenCalled()
  })

  it('scrolls to the section when opened with the hash', async () => {
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    renderWithProviders(<CategorizationSettingsSection />, { route: '/workspace/settings#categorization' })
    await screen.findByRole('switch')
    await waitFor(() => expect(scroll).toHaveBeenCalledTimes(1))
    expect(scroll).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' })
  })

  it('does not scroll without the hash', async () => {
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    renderWithProviders(<CategorizationSettingsSection />)
    await screen.findByRole('switch')
    expect(scroll).not.toHaveBeenCalled()
  })

  it('locks the controls while a save is pending', async () => {
    let resolve!: (v: unknown) => void
    api.categorization.updateSettings.mockReturnValue(new Promise((r) => { resolve = r }))
    const { user } = renderWithProviders(<CategorizationSettingsSection />)
    await user.click(await screen.findByRole('switch'))
    await waitFor(() => expect(screen.getByRole('switch')).toBeDisabled())
    expect(screen.getByTestId('categorization-threshold-95')).toBeDisabled()
    resolve({ ...base, enabled: false })
    await waitFor(() => expect(screen.getByRole('switch')).not.toBeDisabled())
  })
})
