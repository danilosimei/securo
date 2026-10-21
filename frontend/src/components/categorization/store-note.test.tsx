import type { ReactNode } from 'react'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { StoreNote } from '@/components/categorization/store-note'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({ categorization: { setNote: vi.fn() } }))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))

vi.mock('@/lib/api', () => api)
vi.mock('sonner', () => ({ toast }))

function renderNote(canWrite: boolean) {
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <WorkspaceContext.Provider
      value={{ canWrite } as unknown as React.ContextType<typeof WorkspaceContext>}
    >
      {children}
    </WorkspaceContext.Provider>
  )
  return renderWithProviders(
    <Wrapper>
      <StoreNote merchantKey="K" note="only on Fridays" />
    </Wrapper>,
  )
}

beforeEach(() => vi.clearAllMocks())

describe('StoreNote', () => {
  it('shows the note and deletes it with the X', async () => {
    api.categorization.setNote.mockResolvedValue({ merchant_key: 'K', note: null })
    const { user } = renderNote(true)
    expect(screen.getByText(t('categorization.note.label', { note: 'only on Fridays' }))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('categorization.note.delete') }))
    await waitFor(() =>
      expect(api.categorization.setNote).toHaveBeenCalledWith({ merchant_key: 'K', note: null }),
    )
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(t('categorization.note.deleted')))
  })

  it('shows the error toast when deleting fails', async () => {
    api.categorization.setNote.mockRejectedValue(new Error('boom'))
    const { user } = renderNote(true)
    await user.click(screen.getByRole('button', { name: t('categorization.note.delete') }))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('ignores a second click while deleting', async () => {
    let resolve: (v: unknown) => void = () => {}
    api.categorization.setNote.mockReturnValue(new Promise((r) => (resolve = r)))
    const { user } = renderNote(true)
    const x = screen.getByRole('button', { name: t('categorization.note.delete') })
    await user.click(x)
    await user.click(x)
    expect(api.categorization.setNote).toHaveBeenCalledTimes(1)
    resolve({ merchant_key: 'K', note: null })
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
  })

  it('hides the X from a viewer', () => {
    renderNote(false)
    expect(screen.getByText(/only on Fridays/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('categorization.note.delete') })).not.toBeInTheDocument()
  })
})
