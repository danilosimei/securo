/** Which state a Reports block is in, from its query. */
export type BlockState = 'loading' | 'error' | 'empty' | 'ready'

/** The state a block is in, from its query. */
export function blockState(query: { isError: boolean; data: unknown }, isEmpty: boolean): BlockState {
  if (query.isError && !query.data) return 'error'
  if (!query.data) return 'loading'
  return isEmpty ? 'empty' : 'ready'
}
