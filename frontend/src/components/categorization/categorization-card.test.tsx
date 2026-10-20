import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { CategorizationCard } from '@/components/categorization/categorization-card'
import { renderWithProviders } from '@/test/utils'

const hook = vi.hoisted(() => ({ result: { data: undefined as unknown, isLoading: false } }))
const workspace = vi.hoisted(() => ({ canWrite: true, canManage: true }))

vi.mock('@/hooks/use-categorization-summary', () => ({
  useCategorizationSummary: () => hook.result,
}))
vi.mock('@/contexts/workspace-context', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/workspace-context')>()),
  useWorkspace: () => workspace,
}))

const summary = (over: Record<string, unknown> = {}) => ({
  pending_groups: 2,
  pending_transactions: 5,
  applied_last_7d: 47,
  last_run: null,
  ai_available: true,
  month_cost_usd: 0,
  monthly_cap_usd: 1,
  ...over,
})

function renderCard(props: Partial<React.ComponentProps<typeof CategorizationCard>> = {}) {
  const onOpen = vi.fn()
  renderWithProviders(
    <CategorizationCard loading={false} uncategorizedCount={0} onOpenUncategorized={onOpen} {...props} />,
  )
  return onOpen
}

describe('CategorizationCard', () => {
  beforeEach(() => {
    hook.result = { data: summary(), isLoading: false }
    workspace.canManage = true
  })

  it('is amber with review link when there are pending transactions', () => {
    renderCard()
    const card = screen.getByTestId('categorization-card-pending')
    expect(card).toHaveTextContent('5 to review')
    expect(card).toHaveTextContent('47 categorized automatically this week')
    expect(card).toHaveAttribute('href', '/categorization?tab=review')
  })

  it('is green when nothing is pending, with a link to applied', () => {
    hook.result = { data: summary({ pending_transactions: 0, pending_groups: 0 }), isLoading: false }
    renderCard()
    expect(screen.getByTestId('categorization-card-clear')).toHaveTextContent('All categorized')
    expect(screen.getByTestId('categorization-card-clear')).toHaveTextContent('47 automatic this week')
    expect(screen.getByRole('link', { name: 'View applied' })).toHaveAttribute('href', '/categorization?tab=applied')
  })

  it('shows a skeleton while loading', () => {
    hook.result = { data: undefined, isLoading: true }
    renderCard()
    expect(screen.getByTestId('categorization-card-skeleton')).toBeInTheDocument()
  })

  it('shows a skeleton while the dashboard summary loads', () => {
    renderCard({ loading: true })
    expect(screen.getByTestId('categorization-card-skeleton')).toBeInTheDocument()
  })

  it('keeps the legacy uncategorized banner when AI is off, with an enable link for managers', () => {
    hook.result = { data: summary({ ai_available: false }), isLoading: false }
    const onOpen = renderCard({ uncategorizedCount: 12, uncategorizedAmountLabel: 'R$ 100,00' })
    expect(screen.getByTestId('categorization-card-legacy')).toHaveTextContent('12 transactions need categorizing')
    expect(screen.getByTestId('categorization-card-legacy')).toHaveTextContent('R$ 100,00')
    expect(screen.getByRole('link', { name: 'Turn on automatic categorization' })).toHaveAttribute(
      'href',
      '/workspace/settings#categorization',
    )
    screen.getByRole('button', { name: /Categorize now/ }).click()
    expect(onOpen).toHaveBeenCalled()
  })

  it('hides the enable link without canManage', () => {
    workspace.canManage = false
    hook.result = { data: summary({ ai_available: false }), isLoading: false }
    renderCard({ uncategorizedCount: 3 })
    expect(screen.queryByRole('link', { name: 'Turn on automatic categorization' })).toBeNull()
  })

  it('AI off and nothing uncategorized is the plain green state', () => {
    hook.result = { data: summary({ ai_available: false, pending_transactions: 0 }), isLoading: false }
    renderCard()
    expect(screen.getByText('All caught up!')).toBeInTheDocument()
  })
})
