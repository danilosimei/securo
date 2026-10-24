/** The conversation with the AI about one store ("Ask AI"): what blocks it,
 *  how the turns go over the wire and which message an error shows. The
 *  conversation lives in the page (never stored): the client sends it whole
 *  with each message, so the server can hold it to the limit. */
import type { AskMessage, AskResponse, CategorizationSummary } from '@/types'

/** The server refuses a conversation past this many messages of the person's. */
export const ASK_MAX_PERSON_MESSAGES = 30

/** Why the entry points are disabled (each has a `categorization.ask.blocked.*`). */
export type AskBlocked = 'readOnly' | 'disabled' | 'noConnection' | 'capped'

/** The first reason the person cannot ask, or null. Read-only comes first and
 *  needs no summary; the rest wait for it (no summary yet: not blocked). */
export function askBlocked(
  summary: CategorizationSummary | undefined,
  canWrite: boolean,
): AskBlocked | null {
  if (!canWrite) return 'readOnly'
  if (!summary) return null
  if (!summary.ai_enabled) return 'disabled'
  if (!summary.ai_available) return 'noConnection'
  if (Number(summary.month_cost_usd) >= Number(summary.monthly_cap_usd)) return 'capped'
  return null
}

/** One turn on screen. `wire` is what goes back to the API: the person's
 *  text, or the assistant's `assistant_turn` (its whole structured answer). */
export interface ChatTurn {
  role: 'user' | 'assistant'
  text: string
  wire: string
  answer?: AskResponse
}

export function toWire(turns: ChatTurn[]): AskMessage[] {
  return turns.map((turn) => ({ role: turn.role, content: turn.wire }))
}

export function personCount(turns: ChatTurn[]): number {
  return turns.filter((turn) => turn.role === 'user').length
}

const CODES = new Set([
  'conversation_limit',
  'capped',
  'disabled',
  'no_connection',
  'timeout',
  'model_error',
  'not_found',
  'invalid_conversation',
])

/** Errors after which sending again cannot work (the field is disabled). */
export const ASK_HALTING_ERRORS = new Set([
  'categorization.ask.errors.capped',
  'categorization.ask.errors.disabled',
  'categorization.ask.errors.no_connection',
  'categorization.ask.errors.conversation_limit',
])

/** The i18n key of the message for a failed `ask`. */
export function askErrorKey(err: unknown): string {
  const response =
    err && typeof err === 'object' && 'response' in err
      ? (err as { response?: { status?: number; data?: { detail?: unknown } } }).response
      : undefined
  const detail = response?.data?.detail
  const code =
    detail && typeof detail === 'object' && 'code' in detail ? (detail as { code?: unknown }).code : undefined
  if (typeof code === 'string' && CODES.has(code)) return `categorization.ask.errors.${code}`
  if (response?.status === 429 || code === 'rate_limited') return 'categorization.ask.errors.rateLimited'
  // A proxy that gave up first answers 504 without our detail.
  if (response?.status === 504) return 'categorization.ask.errors.timeout'
  return 'categorization.ask.errors.generic'
}

/** A conversation of the page: its turns, whether a message is waiting for
 *  its answer, and its generation. Accepting ends the conversation and
 *  starts a new generation, so an answer still on its way for the old one
 *  is dropped (a fresh conversation always starts with the person's turn). */
export interface Conversation {
  gen: number
  turns: ChatTurn[]
  thinking: boolean
}

export type Conversations = ReadonlyMap<string, Conversation>

const EMPTY: Conversation = { gen: 0, turns: [], thinking: false }

/** One conversation per store and per set of rows: Review talks about the
 *  store's pending rows, each Applied row about itself (another context on
 *  the server, so another conversation). */
export function conversationKey(target: { merchantKey: string; transactionIds?: string[] }): string {
  if (!target.transactionIds) return target.merchantKey
  return `${target.merchantKey}|${[...target.transactionIds].sort().join(',')}`
}

export function conversationOf(state: Conversations, key: string): Conversation {
  return state.get(key) ?? EMPTY
}

/** Changes a conversation of generation `gen`; a stale one is left alone. */
export function updateConversation(
  state: Conversations,
  key: string,
  gen: number,
  change: (current: Conversation) => Partial<Omit<Conversation, 'gen'>>,
): Conversations {
  const current = conversationOf(state, key)
  if (current.gen !== gen) return state
  return new Map(state).set(key, { ...current, ...change(current) })
}

/** Ends a conversation: empty, nothing waiting, and a new generation. */
export function clearConversation(state: Conversations, key: string): Conversations {
  const current = conversationOf(state, key)
  return new Map(state).set(key, { gen: current.gen + 1, turns: [], thinking: false })
}
