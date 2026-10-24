import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { renderWithProviders } from '@/test/utils'
import { RowActions } from './row-actions'

describe('RowActions', () => {
  it('opens a menu with edit, extras and delete, named after the row', async () => {
    const onEdit = vi.fn()
    const onDelete = vi.fn()
    const onExtra = vi.fn()
    const { user } = renderWithProviders(
      <RowActions label="Rent" onEdit={onEdit} onDelete={onDelete} extra={[{ label: 'Pause', onSelect: onExtra }]} />,
    )
    await user.click(screen.getByRole('button', { name: 'More actions: Rent' }))
    const items = (await screen.findAllByRole('menuitem')).map((item) => item.textContent)
    expect(items).toEqual(['Edit', 'Pause', 'Delete'])

    await user.click(screen.getByRole('menuitem', { name: 'Delete' }))
    expect(onDelete).toHaveBeenCalledTimes(1)
    expect(onEdit).not.toHaveBeenCalled()
  })

  it('does not bubble clicks to a clickable row', async () => {
    const onRow = vi.fn()
    const { user } = renderWithProviders(
      <div onClick={onRow}>
        <RowActions label="Rent" onEdit={vi.fn()} />
      </div>,
    )
    await user.click(screen.getByRole('button', { name: /More actions/ }))
    await user.click(await screen.findByRole('menuitem', { name: 'Edit' }))
    expect(onRow).not.toHaveBeenCalled()
  })

  it('renders nothing without actions', () => {
    renderWithProviders(<RowActions label="Rent" />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
