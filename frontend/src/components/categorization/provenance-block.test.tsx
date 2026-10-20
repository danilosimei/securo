import type { ReactNode } from 'react'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ProvenanceBlock } from '@/components/categorization/provenance-block'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { renderWithProviders, t } from '@/test/utils'
import type { Category, Provenance, ProvenanceEvent } from '@/types'

const api = vi.hoisted(() => ({
  categorization: { provenance: vi.fn(), undo: vi.fn() },
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }))

vi.mock('@/lib/api', () => api)
vi.mock('sonner', () => ({ toast }))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

const cat = (id: string, name: string) =>
  ({ id, name, group_id: null, icon: 'circle-help', color: '#10b981' }) as unknown as Category
const restaurants = cat('c1', 'Restaurants')
const groceries = cat('c2', 'Groceries')

const twoDaysAgo = new Date(Date.now() - 2 * 86400 * 1000).toISOString()

function event(overrides: Partial<ProvenanceEvent> = {}): ProvenanceEvent {
  return {
    id: 'e1',
    run_id: 'run1',
    source: 'ai',
    previous_source: 'none',
    previous_category_id: 'c2',
    new_category_id: 'c1',
    confidence: 94,
    reason: 'Food delivery app',
    model: 'claude-haiku-5-5',
    user_id: null,
    created_at: twoDaysAgo,
    undone_at: null,
    ...overrides,
  }
}

function provenance(overrides: Partial<Provenance> = {}): Provenance {
  return {
    current_source: 'ai',
    original_description: 'IFOOD *PEDIDO 123',
    events: [event()],
    ...overrides,
  }
}

function withRole(canWrite: boolean) {
  return function Workspace({ children }: { children: ReactNode }) {
    return (
      <WorkspaceContext.Provider
        value={{ canWrite } as unknown as React.ContextType<typeof WorkspaceContext>}
      >
        {children}
      </WorkspaceContext.Provider>
    )
  }
}

function renderBlock({ canWrite = true, onUndone = vi.fn() } = {}) {
  const Workspace = withRole(canWrite)
  const result = renderWithProviders(
    <Workspace>
      <ProvenanceBlock transactionId="t1" categories={[restaurants, groceries]} onUndone={onUndone} />
    </Workspace>,
  )
  return { ...result, onUndone }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ProvenanceBlock', () => {
  it('shows what was applied and what it was originally', async () => {
    api.categorization.provenance.mockResolvedValue(provenance())
    renderBlock()

    expect(await screen.findByText(t('categorization.provenance.by_ai'))).toBeInTheDocument()
    expect(screen.getByText(/94%/)).toBeInTheDocument()
    expect(screen.getByText(/2 days ago/)).toBeInTheDocument()
    expect(screen.getByText('Food delivery app')).toBeInTheDocument()
    expect(screen.getByText(/IFOOD \*PEDIDO 123/)).toBeInTheDocument()
    expect(screen.getByText(/Groceries/)).toBeInTheDocument()
    expect(api.categorization.provenance).toHaveBeenCalledWith('t1')
  })

  it('says "No category" when nothing was there before', async () => {
    api.categorization.provenance.mockResolvedValue(
      provenance({ events: [event({ previous_category_id: null })] }),
    )
    renderBlock()
    expect(await screen.findByText(new RegExp(t('categorization.provenance.noCategory')))).toBeInTheDocument()
  })

  it('says the category was removed when it is no longer in the list', async () => {
    api.categorization.provenance.mockResolvedValue(
      provenance({ events: [event({ previous_category_id: 'gone' })] }),
    )
    renderBlock()
    expect(await screen.findByText(new RegExp(t('categorization.provenance.removedCategory')))).toBeInTheDocument()
    expect(screen.queryByText(new RegExp(t('categorization.provenance.noCategory')))).not.toBeInTheDocument()
  })

  it('undoes the latest event that is still in effect and says it went back to Review', async () => {
    api.categorization.provenance.mockResolvedValue(
      provenance({
        events: [
          event({ id: 'e-new', source: 'memory', previous_category_id: 'c2' }),
          event({ id: 'e-old', undone_at: null }),
        ],
        current_source: 'memory',
      }),
    )
    api.categorization.undo.mockResolvedValue({ restored: 1, skipped: 0 })
    const { user, onUndone } = renderBlock()

    await user.click(await screen.findByRole('button', { name: t('categorization.provenance.undo') }))

    await waitFor(() => expect(api.categorization.undo).toHaveBeenCalledWith({ event_ids: ['e-new'] }))
    expect(toast.success).toHaveBeenCalledWith(t('categorization.provenance.undoneReview'))
    expect(onUndone).toHaveBeenCalledWith('c2', 'none')
  })

  it('skips events already undone', async () => {
    api.categorization.provenance.mockResolvedValue(
      provenance({
        current_source: 'rule',
        events: [
          event({ id: 'e-undone', undone_at: twoDaysAgo }),
          event({ id: 'e-rule', source: 'rule', confidence: null, reason: null }),
        ],
      }),
    )
    api.categorization.undo.mockResolvedValue({ restored: 1, skipped: 0 })
    const { user } = renderBlock()

    // Rule origins start collapsed: open first.
    await user.click(await screen.findByTitle(t('categorization.provenance.toggle')))
    await user.click(screen.getByRole('button', { name: t('categorization.provenance.undo') }))
    await waitFor(() => expect(api.categorization.undo).toHaveBeenCalledWith({ event_ids: ['e-rule'] }))
    expect(toast.success).toHaveBeenCalledWith(t('categorization.provenance.undone'))
  })

  it('tells the person when there was nothing left to undo', async () => {
    api.categorization.provenance.mockResolvedValue(provenance())
    api.categorization.undo.mockResolvedValue({ restored: 0, skipped: 1 })
    const { user, onUndone } = renderBlock()
    await user.click(await screen.findByRole('button', { name: t('categorization.provenance.undo') }))
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith(t('categorization.provenance.undoSkipped')))
    expect(onUndone).not.toHaveBeenCalled()
  })

  it.each(['ai', 'memory'] as const)('starts expanded for %s', async (source) => {
    api.categorization.provenance.mockResolvedValue(
      provenance({ current_source: source, events: [event({ source })] }),
    )
    renderBlock()
    expect(await screen.findByText('Food delivery app')).toBeInTheDocument()
    expect(screen.getByTitle(t('categorization.provenance.toggle'))).toHaveAttribute(
      'aria-expanded',
      'true',
    )
  })

  it.each(['rule', 'provider'] as const)('starts collapsed in one line for %s', async (source) => {
    api.categorization.provenance.mockResolvedValue(
      provenance({ current_source: source, events: [event({ source, reason: 'Matched rule iFood' })] }),
    )
    const { user } = renderBlock()
    const toggle = await screen.findByTitle(t('categorization.provenance.toggle'))
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText(t(`categorization.provenance.by_${source}`))).toBeInTheDocument()
    expect(screen.queryByText('Matched rule iFood')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('categorization.provenance.undo') })).not.toBeInTheDocument()

    await user.click(toggle)
    expect(screen.getByText('Matched rule iFood')).toBeInTheDocument()
  })

  it('shows a bank category with no events, without an undo', async () => {
    api.categorization.provenance.mockResolvedValue(provenance({ current_source: 'provider', events: [] }))
    const { user } = renderBlock()
    await user.click(await screen.findByTitle(t('categorization.provenance.toggle')))
    expect(screen.getByText(/IFOOD \*PEDIDO 123/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('categorization.provenance.undo') })).not.toBeInTheDocument()
  })

  it.each(['user', 'none'] as const)('renders nothing for %s with no events', async (source) => {
    api.categorization.provenance.mockResolvedValue(provenance({ current_source: source, events: [] }))
    const { container } = renderBlock()
    await waitFor(() => expect(api.categorization.provenance).toHaveBeenCalled())
    // Let the query settle before asserting the absence.
    await new Promise((r) => setTimeout(r, 0))
    expect(container).toBeEmptyDOMElement()
  })

  it('renders for a person-made category when there is history', async () => {
    api.categorization.provenance.mockResolvedValue(
      provenance({ current_source: 'user', events: [event({ source: 'user', confidence: null, reason: null })] }),
    )
    renderBlock()
    expect(await screen.findByText(t('categorization.provenance.by_user'))).toBeInTheDocument()
  })

  it('hides the undo from a viewer', async () => {
    api.categorization.provenance.mockResolvedValue(provenance())
    renderBlock({ canWrite: false })
    expect(await screen.findByText('Food delivery app')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('categorization.provenance.undo') })).not.toBeInTheDocument()
  })
})
