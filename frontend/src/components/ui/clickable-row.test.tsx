import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { clickableRow } from '@/components/ui/clickable-row'

function Row({ onOpen }: { onOpen?: () => void }) {
  return (
    <div {...clickableRow(onOpen)} data-testid="row">
      Rent
      <button type="button" onClick={(event) => event.stopPropagation()}>More</button>
    </div>
  )
}

describe('clickableRow', () => {
  it('opens on click and on Enter, and is a button for the keyboard', async () => {
    const onOpen = vi.fn()
    const user = userEvent.setup()
    render(<Row onOpen={onOpen} />)

    const row = screen.getByRole('button', { name: /Rent/ })
    await user.click(row)
    expect(onOpen).toHaveBeenCalledTimes(1)

    row.focus()
    await user.keyboard('{Enter}')
    expect(onOpen).toHaveBeenCalledTimes(2)
  })

  it('ignores keys pressed on a button inside the row', async () => {
    const onOpen = vi.fn()
    const user = userEvent.setup()
    render(<Row onOpen={onOpen} />)

    screen.getByRole('button', { name: 'More' }).focus()
    await user.keyboard('{Enter}')
    expect(onOpen).not.toHaveBeenCalled()
  })

  it('does nothing without an action', () => {
    render(<Row />)
    expect(screen.getByTestId('row')).not.toHaveAttribute('role')
  })
})
