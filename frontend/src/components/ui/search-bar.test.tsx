import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { renderWithProviders } from '@/test/utils'
import { SearchBar } from './search-bar'

describe('SearchBar', () => {
  it('reports what is typed and clears with the X', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(<SearchBar value="" onChange={onChange} placeholder="Search people" />)
    await user.type(screen.getByRole('searchbox', { name: 'Search people' }), 'a')
    expect(onChange).toHaveBeenLastCalledWith('a')
    expect(screen.queryByRole('button', { name: 'Clear search' })).not.toBeInTheDocument()
  })

  it('shows the X only with text and empties the search', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(<SearchBar value="rent" onChange={onChange} placeholder="Search" />)
    await user.click(screen.getByRole('button', { name: 'Clear search' }))
    expect(onChange).toHaveBeenCalledWith('')
  })
})
