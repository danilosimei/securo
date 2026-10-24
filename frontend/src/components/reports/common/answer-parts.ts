/** The parts of a drawn answer sentence (see `answer-line.tsx`): the
 *  sentence comes from the translations, so every language keeps its own
 *  word order. `answerParts` asks i18next for the sentence with a marker in
 *  place of each value and cuts it at the markers. */
import type { TFunction } from 'i18next'
import { formatPercent } from './chart-utils'

export type AnswerToken =
  /** Money or a count: bold, masked by the caller in privacy mode. */
  | { kind: 'value'; text: string }
  /** A relative change (ratio); `good` paints it green, else red. */
  | { kind: 'delta'; change: number; good: boolean; locale: string }
  /** A name that opens Explore (or another page). */
  | { kind: 'link'; text: string; to: string; state?: unknown }

export type AnswerPart = string | AnswerToken

const OPEN = '⟦'
const CLOSE = '⟧'
const MARKER = new RegExp(`${OPEN}(\\w+)${CLOSE}`)

/** The parts of the translated sentence `key`: plain text, and the tokens
 *  where its placeholders were. A plain string or number fills a
 *  placeholder as text. `options` go to i18next as they are (`count`). */
export function answerParts(
  t: TFunction,
  key: string,
  tokens: Record<string, AnswerToken | string | number>,
  options: Record<string, unknown> = {},
): AnswerPart[] {
  const markers = Object.fromEntries(Object.keys(tokens).map((name) => [name, `${OPEN}${name}${CLOSE}`]))
  const text = t(key, { ...options, ...markers }) as string
  const out: AnswerPart[] = []
  text.split(MARKER).forEach((piece, i) => {
    if (i % 2 === 0) {
      if (piece) out.push(piece)
      return
    }
    const token = tokens[piece]
    if (token === undefined) return
    out.push(typeof token === 'object' ? token : String(token))
  })
  return out
}

/** The sentence as plain text, for an `aria-label` or a test. */
export function answerText(parts: AnswerPart[]): string {
  return parts
    .map((part) => {
      if (typeof part === 'string') return part
      if (part.kind === 'delta') return formatPercent(Math.abs(part.change), part.locale)
      return part.text
    })
    .join('')
}
