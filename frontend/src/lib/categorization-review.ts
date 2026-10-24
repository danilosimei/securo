/** Naming helpers for the categorization review queue. */
import type { ReviewGroup } from '@/types'

const PERSON_PREFIX = 'PIX:'
const HASHED_PERSON_PREFIX = 'PIX:#'

function titleCase(text: string) {
  return text.toLowerCase().replace(/(^|\s)\p{L}/gu, (m) => m.toUpperCase())
}

/** The name a person recognises: the cleaned name, else a readable key.
 *  A hashed Pix key (`PIX:#…`, a document digest) is never shown. */
export function storeName(group: ReviewGroup): string | null {
  if (group.clean_name) return group.clean_name
  if (group.merchant_key.startsWith(HASHED_PERSON_PREFIX)) return null
  if (group.merchant_key.startsWith(PERSON_PREFIX)) {
    return titleCase(group.merchant_key.slice(PERSON_PREFIX.length).trim())
  }
  return group.merchant_key
}
