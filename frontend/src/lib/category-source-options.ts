/** Origins offered by the "Origem" transactions filter (desktop and mobile). */
export const CATEGORY_SOURCE_OPTIONS = [
  { value: 'ai', labelKey: 'categorization.origin.ai' },
  { value: 'memory', labelKey: 'categorization.origin.memory' },
  { value: 'rule', labelKey: 'categorization.origin.rule' },
  { value: 'user', labelKey: 'categorization.origin.filterUser' },
  { value: 'provider', labelKey: 'categorization.origin.filterProvider' },
  { value: 'none', labelKey: 'categorization.origin.filterNone' },
] as const

/** Parses the `category_source` URL param into a clean list. */
export function parseCategorySourceParam(raw: string | null): string[] {
  return raw ? raw.split(',').filter(Boolean) : []
}
