import { createElement, type ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query'

import {
  categoryChanged,
  singleStoreReference,
  useCategoryCorrectionToast,
  withUndoneCategory,
} from '@/hooks/use-category-correction-toast'
import type { RuleDialogInitialData } from '@/components/rule-dialog'
import { createTestQueryClient, t } from '@/test/utils'

const api = vi.hoisted(() => ({
  categorization: { similar: vi.fn(), applySimilar: vi.fn(), ruleDraft: vi.fn(), undo: vi.fn() },
}))
const toast = vi.hoisted(() =>
  Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), info: vi.fn() }),
)
vi.mock('@/lib/api', () => api)
vi.mock('sonner', () => ({ toast }))

let queryClient: QueryClient

function render() {
  const onCreateRule = vi.fn<(initialData: RuleDialogInitialData) => void>()
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children)
  const hook = renderHook(() => useCategoryCorrectionToast({ onCreateRule }), { wrapper })
  return { ...hook, onCreateRule }
}

type ToastOptions = {
  id?: string | number
  action?: { label: string; onClick: (e?: unknown) => void }
  cancel?: { label: string; onClick: (e?: unknown) => void }
  duration?: number
}

/** The options of the last toast.success call carrying actions. */
function lastActionToast(): ToastOptions | undefined {
  const calls = toast.success.mock.calls.filter(([, opts]) => opts && ('action' in opts || 'cancel' in opts))
  return calls[calls.length - 1]?.[1] as ToastOptions | undefined
}

const draft = {
  name: 'IFOOD',
  conditions: [{ field: 'description', op: 'contains', value: 'IFOOD' }],
  actions: [{ op: 'set_category', value: 'c1' }],
}

beforeEach(() => {
  vi.clearAllMocks()
  queryClient = createTestQueryClient()
  toast.success.mockReturnValue('toast-1')
  api.categorization.similar.mockResolvedValue({ merchant_key: 'IFOOD', count: 3, transaction_ids: ['a', 'b', 'c'] })
  api.categorization.ruleDraft.mockResolvedValue(draft)
  api.categorization.applySimilar.mockResolvedValue({ applied: 3, event_ids: ['e1', 'e2', 'e3'], event_run_id: null })
  api.categorization.undo.mockResolvedValue({ restored: 3, skipped: 0 })
})

describe('useCategoryCorrectionToast', () => {
  it('offers to apply to the N similar ones and to create a rule', async () => {
    const { result } = render()
    await result.current.notifyCorrection({ id: 't1' })

    expect(api.categorization.similar).toHaveBeenCalledWith('t1')
    expect(api.categorization.ruleDraft).toHaveBeenCalledWith('t1')
    const opts = lastActionToast()
    expect(opts?.action?.label).toBe(t('categorization.correction.applySimilar', { count: 3 }))
    expect(opts?.cancel?.label).toBe(t('categorization.correction.createRule'))
    expect(opts?.duration).toBe(8000)
    // Updates the toast already on screen instead of stacking a second one.
    expect(opts?.id).toBe('toast-1')
    // Long from the start, so it cannot expire while the offers load.
    expect(toast.success).toHaveBeenNthCalledWith(1, t('categorization.correction.updated'), { duration: 8000 })
  })

  it('applies to the similar ones and offers to undo them', async () => {
    const { result } = render()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    await result.current.notifyCorrection({ id: 't1' })

    lastActionToast()!.action!.onClick()
    await waitFor(() => expect(api.categorization.applySimilar).toHaveBeenCalledWith('t1'))
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        t('categorization.correction.applied', { count: 3 }),
        expect.objectContaining({ action: expect.objectContaining({ label: t('categorization.correction.undo') }) }),
      ),
    )
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['categorization'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['transactions'] })

    lastActionToast()!.action!.onClick()
    await waitFor(() => expect(api.categorization.undo).toHaveBeenCalledWith({ event_ids: ['e1', 'e2', 'e3'] }))
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(t('categorization.correction.undone', { count: 3 })),
    )
  })

  it('reports a failed apply', async () => {
    api.categorization.applySimilar.mockRejectedValue(new Error('boom'))
    const { result } = render()
    await result.current.notifyCorrection({ id: 't1' })
    lastActionToast()!.action!.onClick()
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
  })

  it('opens the rule dialog pre-filled from the draft, applying to existing rows', async () => {
    const { result, onCreateRule } = render()
    await result.current.notifyCorrection({ id: 't1' })
    lastActionToast()!.cancel!.onClick()
    expect(onCreateRule).toHaveBeenCalledWith({
      name: 'IFOOD',
      conditions: draft.conditions,
      actions: draft.actions,
      applyToExisting: true,
    })
  })

  it('without similar ones, offers only to create a rule', async () => {
    api.categorization.similar.mockResolvedValue({ merchant_key: 'IFOOD', count: 0, transaction_ids: [] })
    const { result, onCreateRule } = render()
    await result.current.notifyCorrection({ id: 't1' })
    const opts = lastActionToast()
    expect(opts?.action?.label).toBe(t('categorization.correction.createRule'))
    expect(opts?.cancel).toBeUndefined()
    opts!.action!.onClick()
    expect(onCreateRule).toHaveBeenCalled()
    expect(api.categorization.applySimilar).not.toHaveBeenCalled()
  })

  it('hides "Create rule" when the store has no safe pattern (422)', async () => {
    api.categorization.ruleDraft.mockRejectedValue({ response: { status: 422 } })
    const { result } = render()
    await result.current.notifyCorrection({ id: 't1' })
    const opts = lastActionToast()
    expect(opts?.action?.label).toBe(t('categorization.correction.applySimilar', { count: 3 }))
    expect(opts?.cancel).toBeUndefined()
  })

  it('keeps the plain confirmation when there is nothing to offer', async () => {
    api.categorization.similar.mockResolvedValue({ merchant_key: 'UNKNOWN', count: 0, transaction_ids: [] })
    api.categorization.ruleDraft.mockRejectedValue({ response: { status: 422 } })
    const { result } = render()
    await result.current.notifyCorrection({ id: 't1' })
    expect(toast.success).toHaveBeenCalledTimes(1)
    expect(toast.success).toHaveBeenCalledWith(t('categorization.correction.updated'), { duration: 8000 })
  })

  it('uses the caller title (bulk)', async () => {
    const { result } = render()
    await result.current.notifyCorrection({ id: 't1' }, { title: '4 updated' })
    expect(toast.success).toHaveBeenNthCalledWith(1, '4 updated', { duration: 8000 })
  })
})

describe('categoryChanged', () => {
  it('is true only when a new, non-empty category was saved', () => {
    expect(categoryChanged({ category_id: 'c1' }, { category_id: 'c2' })).toBe(true)
    expect(categoryChanged({ category_id: null }, { category_id: 'c2' })).toBe(true)
    expect(categoryChanged({ category_id: 'c1' }, { category_id: 'c1' })).toBe(false)
    expect(categoryChanged({ category_id: 'c1' }, { category_id: null })).toBe(false)
    expect(categoryChanged({ category_id: 'c1' }, {})).toBe(false)
    expect(categoryChanged(null, { category_id: 'c2' })).toBe(false)
  })
})

describe('singleStoreReference', () => {
  const tx = (id: string, description: string, payee_id: string | null = null) => ({ id, description, payee_id })

  it('picks the first row when every row is the same store', () => {
    expect(singleStoreReference([tx('a', 'IFOOD *PEDIDO'), tx('b', ' ifood  *pedido ')])?.id).toBe('a')
    expect(singleStoreReference([tx('a', 'IFOOD', 'p1'), tx('b', 'ifood', 'p1')])?.id).toBe('a')
  })

  it('skips selections that may span several stores', () => {
    expect(singleStoreReference([tx('a', 'IFOOD'), tx('b', 'UBER')])).toBeNull()
    // A shared payee is not enough: the store key may come from the description.
    expect(singleStoreReference([tx('a', 'X', 'p1'), tx('b', 'Y', 'p1')])).toBeNull()
    // Nor the same description under different payees (or one without).
    expect(singleStoreReference([tx('a', 'PIX', 'p1'), tx('b', 'PIX', 'p2')])).toBeNull()
    expect(singleStoreReference([tx('a', 'PIX', 'p1'), tx('b', 'PIX')])).toBeNull()
    expect(singleStoreReference([tx('a', '  '), tx('b', '')])).toBeNull()
    expect(singleStoreReference([])).toBeNull()
  })
})

describe('withUndoneCategory', () => {
  const row = { id: 't1', category_id: 'c1', category_source: 'ai' as const }

  it('follows the undo on the row being edited', () => {
    expect(withUndoneCategory(row, 't1', 'c2', 'none')).toEqual({ id: 't1', category_id: 'c2', category_source: 'none' })
    expect(withUndoneCategory(row, 't1', null, null)).toEqual({ id: 't1', category_id: null, category_source: 'ai' })
  })

  it('leaves another row (or none) alone', () => {
    expect(withUndoneCategory(row, 't2', 'c2', 'none')).toBe(row)
    expect(withUndoneCategory(null, 't1', 'c2', 'none')).toBeNull()
  })
})
