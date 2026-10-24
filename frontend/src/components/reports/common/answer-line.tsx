/** The sentence a Reports tab opens with, drawn instead of plain text: the
 *  numbers stand out, a change is a small pill (green when it is good for
 *  the person, red when it is not), and the names open Explore.
 *
 *  The sentence comes from the translations, so every language keeps its own
 *  word order: `answerParts` asks i18next for the sentence with a marker in
 *  place of each value and cuts it at the markers. */
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Skeleton } from '@/components/ui/skeleton'
import { formatPercent } from './chart-utils'
import type { AnswerPart } from './answer-parts'


/** Sentences can start with a month or weekday name, which some languages
 *  write in lower case: the first letter goes up. A first word with
 *  capitals of its own is a name ("iFood") and stays as it is. */
function capitalized(parts: AnswerPart[]): AnswerPart[] {
  const [first, ...rest] = parts
  if (typeof first !== 'string' || !first) return parts
  const word = first.split(/\s/, 1)[0]
  if (word !== word.toLocaleLowerCase()) return parts
  return [first.charAt(0).toLocaleUpperCase() + first.slice(1), ...rest]
}

export function DeltaPill({ change, good, locale }: { change: number; good: boolean; locale: string }) {
  const up = change > 0
  return (
    <span
      data-testid="delta-pill"
      data-tone={good ? 'good' : 'bad'}
      className={cn(
        'inline-flex items-baseline gap-0.5 whitespace-nowrap rounded-full px-1.5 py-px align-baseline text-[0.8em] font-semibold tabular-nums',
        good
          ? 'bg-emerald-500/10 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300'
          : 'bg-rose-500/10 text-rose-700 dark:bg-rose-400/10 dark:text-rose-300',
      )}
    >
      <span aria-hidden="true" className="text-[0.85em]">{up ? '▲' : '▼'}</span>
      {formatPercent(Math.abs(change), locale)}
    </span>
  )
}

export function AnswerParts({ parts }: { parts: AnswerPart[] }) {
  return (
    <>
      {capitalized(parts).map((part, i) => {
        if (typeof part === 'string') return <span key={i}>{part}</span>
        if (part.kind === 'value') {
          return (
            <span key={i} className="font-semibold tabular-nums text-foreground">
              {part.text}
            </span>
          )
        }
        if (part.kind === 'delta') return <DeltaPill key={i} change={part.change} good={part.good} locale={part.locale} />
        return (
          <Link
            key={i}
            to={part.to}
            state={part.state}
            className="rounded-sm font-medium text-foreground underline decoration-foreground/25 decoration-1 underline-offset-[5px] transition-colors hover:decoration-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {part.text}
          </Link>
        )
      })}
    </>
  )
}

interface AnswerLineProps {
  parts: AnswerPart[] | null
  icon: LucideIcon
  loading?: boolean
  stale?: boolean
  /** Small print under the sentence (the period it covers, a note). */
  note?: ReactNode
  className?: string
}

/** The answer a tab opens with: an icon for what it is about, then the
 *  sentence. Loading shows two lines of skeleton (they appear late, so a
 *  fast answer never flashes); a refetch keeps the old sentence, dimmed. */
export function AnswerLine({ parts, icon: Icon, loading, stale, note, className }: AnswerLineProps) {
  if (loading) {
    return (
      <div aria-busy="true" className={cn('flex items-start gap-3', className)}>
        <Skeleton className="size-9 shrink-0 rounded-full" />
        <div className="w-full space-y-2 pt-1">
          <Skeleton className="h-5 w-full max-w-xl" />
          <Skeleton className="h-5 w-2/3 max-w-md" />
        </div>
      </div>
    )
  }
  if (!parts || parts.length === 0) return null
  return (
    <div
      className={cn('flex items-start gap-3 transition-opacity duration-200', stale && 'opacity-60', className)}
      aria-busy={stale || undefined}
    >
      <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Icon size={18} aria-hidden="true" />
      </span>
      <div className="min-w-0">
        <p
          data-testid="report-headline"
          aria-live="polite"
          className="max-w-3xl text-base leading-relaxed text-foreground/90 text-pretty sm:text-lg"
        >
          <AnswerParts parts={parts} />
        </p>
        {note && <p className="mt-1 text-xs text-muted-foreground">{note}</p>}
      </div>
    </div>
  )
}
