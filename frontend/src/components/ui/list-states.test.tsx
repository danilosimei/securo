import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { Inbox } from 'lucide-react'

import { renderWithProviders } from '@/test/utils'
import { EmptyState } from './empty-state'
import { ErrorState } from './error-state'
import { ListSkeleton } from './list-skeleton'
import { Pagination } from './pagination'

describe('list states', () => {
  it('EmptyState shows the phrase and the main action', () => {
    renderWithProviders(<EmptyState icon={Inbox} title="Nothing here" action={<button>Create</button>} />)
    expect(screen.getByText('Nothing here')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create' })).toBeInTheDocument()
  })

  it('ErrorState offers to try again', async () => {
    const onRetry = vi.fn()
    const { user } = renderWithProviders(<ErrorState error={new Error('x')} onRetry={onRetry} />)
    expect(screen.getByRole('alert')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('ListSkeleton renders the asked number of rows', () => {
    renderWithProviders(<ListSkeleton rows={4} />)
    expect(screen.getByTestId('list-skeleton').children).toHaveLength(4)
  })

  it('Pagination pages through and hides on a short list', async () => {
    const onPageChange = vi.fn()
    const { user, unmount } = renderWithProviders(
      <Pagination page={2} pageSize={10} total={35} onPageChange={onPageChange} onPageSizeChange={vi.fn()} />,
    )
    expect(screen.getByText('2 / 4')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(onPageChange).toHaveBeenCalledWith(3)
    await user.click(screen.getByRole('button', { name: 'Previous' }))
    expect(onPageChange).toHaveBeenCalledWith(1)
    unmount()

    const short = renderWithProviders(
      <Pagination page={1} pageSize={10} total={8} onPageChange={vi.fn()} onPageSizeChange={vi.fn()} />,
    )
    expect(short.container).toBeEmptyDOMElement()
  })
})
