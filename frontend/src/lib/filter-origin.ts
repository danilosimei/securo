/** Screens that open Transactions with filters already set. They add
 *  `?from_screen=<one of these>` to the link. */
export const FILTER_ORIGINS = ['reports', 'dashboard', 'group', 'payee'] as const
export type FilterOrigin = (typeof FILTER_ORIGINS)[number]

export function parseFilterOrigin(value: string | null): FilterOrigin | null {
  return FILTER_ORIGINS.find((origin) => origin === value) ?? null
}
