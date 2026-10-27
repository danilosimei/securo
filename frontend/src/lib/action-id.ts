/** The id of one action (a save, "apply to the similar ones", an undo), sent
 *  as `X-Action-Id` so the screen can undo it whole or link to its line in
 *  Categorization > Histórico. `crypto.randomUUID` needs a secure context,
 *  which the app served over plain http on the home network is not;
 *  `getRandomValues` works everywhere. */
export function newActionId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

/** Request options naming the action, or none. */
export function actionHeaders(actionId?: string): { headers?: Record<string, string> } {
  return actionId ? { headers: { 'X-Action-Id': actionId } } : {}
}
