import { Brain, Landmark, Sparkles, Wand2, type LucideIcon } from 'lucide-react'

export interface OriginStyle {
  Icon: LucideIcon
  /** Pill colors (border, background, text), light and dark. */
  tone: string
  /** Text color for a bare icon. */
  iconClass: string
}

/**
 * One look per automatic origin, shared by the list seal, the audit list and
 * the "how it got here" block, so an origin reads the same everywhere.
 */
export const ORIGIN_STYLES = {
  ai: {
    Icon: Sparkles,
    tone: 'border-violet-200 bg-violet-50 text-violet-700 dark:border-violet-900 dark:bg-violet-950/40 dark:text-violet-300',
    iconClass: 'text-violet-600 dark:text-violet-400',
  },
  memory: {
    Icon: Brain,
    tone: 'border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-300',
    iconClass: 'text-sky-600 dark:text-sky-400',
  },
  rule: {
    Icon: Wand2,
    tone: 'border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900/40 dark:text-slate-300',
    iconClass: 'text-slate-500 dark:text-slate-400',
  },
  provider: {
    Icon: Landmark,
    tone: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300',
    iconClass: 'text-emerald-600 dark:text-emerald-400',
  },
} as const satisfies Record<string, OriginStyle>
