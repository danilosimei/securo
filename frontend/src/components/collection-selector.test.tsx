import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { CollectionSelector } from '@/components/collection-selector'
import { renderWithProviders, t } from '@/test/utils'

const filter = vi.hoisted(() => ({ value: {} as Record<string, unknown> }))
vi.mock('@/contexts/collection-filter-context', () => ({ useCollectionFilter: () => filter.value }))

const trip = { id: 'c1', name: 'Viagem', color: '#3b82f6', account_count: 2, wallet_count: 0 }

beforeEach(() => {
  filter.value = { collections: [], activeCollection: null, setActiveCollectionId: vi.fn() }
})

describe('CollectionSelector', () => {
  it('stays out of the way until there is a collection', () => {
    renderWithProviders(<CollectionSelector />)
    expect(screen.queryByTestId('collection-selector')).not.toBeInTheDocument()
  })

  it('shows the active collection in the menu and lets the user go back to all accounts', async () => {
    const setActiveCollectionId = vi.fn()
    filter.value = { collections: [trip], activeCollection: trip, setActiveCollectionId }
    const { user } = renderWithProviders(<CollectionSelector />)
    const trigger = screen.getByTestId('collection-selector')
    expect(trigger).toHaveTextContent('Viagem')
    await user.click(trigger)
    await user.click(await screen.findByText(t('collections.allAccounts')))
    expect(setActiveCollectionId).toHaveBeenCalledWith(null)
  })
})
