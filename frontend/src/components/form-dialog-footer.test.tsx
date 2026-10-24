import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { renderWithProviders } from '@/test/utils'
import { FormDialogFooter } from './form-dialog-footer'

function renderFooter(props: Parameters<typeof FormDialogFooter>[0]) {
  return renderWithProviders(
    <Dialog open>
      <DialogContent>
        <DialogTitle>Edit</DialogTitle>
        <FormDialogFooter {...props} />
      </DialogContent>
    </Dialog>,
  )
}

describe('FormDialogFooter', () => {
  it('has no Delete on a new item', () => {
    renderFooter({ onCancel: vi.fn(), onSave: vi.fn() })
    expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument()
  })

  it('offers Delete when editing and calls it', async () => {
    const onDelete = vi.fn()
    const { user } = renderFooter({ onCancel: vi.fn(), onSave: vi.fn(), onDelete })
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(onDelete).toHaveBeenCalledTimes(1)
  })
})
