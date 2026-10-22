/** The colors of a highlight's tone. */
import type { HighlightTone } from '@/lib/api'

/** Status colors, always next to the tone's name and an icon. */
export const TONE_STYLE: Record<HighlightTone, { strip: string; chip: string; text: string }> = {
  alert: {
    strip: 'bg-rose-500 dark:bg-rose-400',
    chip: 'bg-rose-500/10 text-rose-600 dark:bg-rose-400/10 dark:text-rose-400',
    text: 'text-rose-600 dark:text-rose-400',
  },
  opportunity: {
    strip: 'bg-emerald-500 dark:bg-emerald-400',
    chip: 'bg-emerald-500/10 text-emerald-600 dark:bg-emerald-400/10 dark:text-emerald-400',
    text: 'text-emerald-600 dark:text-emerald-400',
  },
  info: {
    strip: 'bg-sky-500 dark:bg-sky-400',
    chip: 'bg-sky-500/10 text-sky-600 dark:bg-sky-400/10 dark:text-sky-400',
    text: 'text-sky-600 dark:text-sky-400',
  },
}
