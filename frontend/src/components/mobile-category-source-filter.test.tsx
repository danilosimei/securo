import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { MobileCategorySourceView } from '@/components/mobile-transactions-filter-menu'
import { parseCategorySourceParam } from '@/lib/category-source-options'
import { renderWithProviders, t } from '@/test/utils'

describe('mobile Origem filter', () => {
  it('selects and clears origins', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    renderWithProviders(
      <DropdownMenu open>
        <DropdownMenuTrigger>open</DropdownMenuTrigger>
        <DropdownMenuContent>
          <MobileCategorySourceView selected={['memory']} onChange={onChange} />
        </DropdownMenuContent>
      </DropdownMenu>,
    )

    await user.click(await screen.findByText(t('categorization.origin.ai')))
    expect(onChange).toHaveBeenLastCalledWith(['memory', 'ai'])

    await user.click(screen.getByText(t('categorization.origin.memory')))
    expect(onChange).toHaveBeenLastCalledWith([])

    await user.click(screen.getByText(t('transactions.filtersBar.clearSelection')))
    expect(onChange).toHaveBeenLastCalledWith([])
  })
})

describe('parseCategorySourceParam', () => {
  it('splits the comma list and ignores empties', () => {
    expect(parseCategorySourceParam('ai,memory')).toEqual(['ai', 'memory'])
    expect(parseCategorySourceParam('ai,,')).toEqual(['ai'])
    expect(parseCategorySourceParam(null)).toEqual([])
    expect(parseCategorySourceParam('')).toEqual([])
  })
})
