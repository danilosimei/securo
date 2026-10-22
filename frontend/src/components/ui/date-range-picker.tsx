import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertCircle, Calendar as CalendarIcon } from 'lucide-react'
import { addYears } from 'date-fns'

import { Button } from '@/components/ui/button'
import { Calendar } from '@/components/ui/calendar'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import type { Locale } from 'date-fns'
import { cn } from '@/lib/utils'
import { localDateString } from '@/lib/date-utils'
import { formatDateRange } from '@/lib/date-range-format'
import { formatDateInput, looksCompleteDate, parseDateInput } from '@/lib/date-input'
import { resolveDateFnsLocale } from '@/lib/date-fns-locale'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'

export interface DateRangePickerProps {
  /** Inclusive start date as YYYY-MM-DD, or empty string when unset. */
  from: string
  /** Inclusive end date as YYYY-MM-DD, or empty string when unset. */
  to: string
  /** Fired when the user confirms a new range. Empty strings clear it. */
  onChange: (from: string, to: string) => void
  /** Optional label used in the trigger and popover header. */
  label?: string
  /** Extra classes applied to the trigger button. */
  className?: string
  /** Compact styling suitable for report toolbars. */
  size?: 'default' | 'sm'
  /** Trigger content when nothing is selected. */
  placeholder?: string
  /**
   * 'segment' renders the trigger as a plain segment cell (no border/icon of
   * its own) so it can sit inside a segmented control, e.g. a range-preset
   * button group, instead of appearing as a separate floating control.
   */
  variant?: 'button' | 'segment'
  /** Segment highlight state; ignored for variant 'button'. */
  active?: boolean
  /**
   * Fired as soon as the popover starts opening — before the calendar is
   * shown. This is a notification only; committed values change on Apply.
   */
  onOpen?: () => void
  /**
   * Seed the drafts when the picker opens with no committed selection.
   * Defaults are committed only when the user clicks Apply.
   */
  defaultFrom?: string
  defaultTo?: string
  /** Reject a candidate range whose (normalized) end date is after today. */
  disallowFuture?: boolean
  /**
   * Reject a candidate range whose end date is more than this many calendar
   * years after the start date (the 10-year anniversary, not a fixed day
   * count — leap days inside the window would otherwise let a day-count cap
   * drift past the intended boundary).
   */
  maxRangeYears?: number
}

/**
 * The one "from → to" date picker: a single trigger + popover. Reports, the
 * Transactions filter and a credit card's cycle all pick a range through it
 * (the last two host `DateRangePanel` in their own popover).
 */
export function DateRangePicker({
  from,
  to,
  onChange,
  label,
  className,
  size = 'sm',
  placeholder,
  variant = 'button',
  active = false,
  onOpen,
  defaultFrom,
  defaultTo,
  disallowFuture = false,
  maxRangeYears,
}: DateRangePickerProps) {
  const { t, i18n } = useTranslation()
  const dateLocale = useDisplayLocale()
  const dateFnsLocale = resolveDateFnsLocale(i18n.resolvedLanguage ?? i18n.language)
  const [open, setOpen] = useState(false)
  // What the panel starts from each time the popover opens.
  const [seed, setSeed] = useState({ from, to })

  // Seed the drafts every time the popover opens so a preset switch above the
  // picker (which mutates the controlled `from`/`to` props) doesn't surface
  // stale values the next time the user opens it. Closing without Apply
  // leaves the controlled range intact.
  const handleOpenChange = (next: boolean) => {
    if (next) {
      onOpen?.()
      setSeed(from || to ? { from, to } : { from: defaultFrom ?? from, to: defaultTo ?? to })
    }
    setOpen(next)
  }

  const headerLabel = label ?? t('transactions.filtersBar.customRange')
  const emptyLabel =
    placeholder ?? t('transactions.filtersBar.pickRange')
  const triggerLabel =
    from || to ? formatDateRange(from, to, dateLocale) : emptyLabel
  // Segment mode sits inline among short preset labels (6M, 1Y, …), so it
  // drops the year even across a year boundary — the same compact form the
  // transactions filter bar's applied-range chip uses. The year is still
  // visible while the calendar is open, which is the only place picking it
  // actually matters.
  const compactLabel =
    from || to ? formatDateRange(from, to, dateLocale, { compact: true }) : emptyLabel

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        {variant === 'segment' ? (
          <button
            type="button"
            className={cn(
              'px-3 py-1.5 text-xs font-semibold whitespace-nowrap transition-colors',
              active
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
              className,
            )}
            aria-label={headerLabel}
          >
            {active ? compactLabel : (label ?? emptyLabel)}
          </button>
        ) : (
          <Button
            type="button"
            variant="outline"
            size={size}
            className={cn(
              'gap-1.5 whitespace-nowrap font-normal',
              !(from || to) && 'text-muted-foreground',
              className,
            )}
            aria-label={headerLabel}
          >
            <CalendarIcon size={14} />
            {triggerLabel}
          </Button>
        )}
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={8}
        className="w-auto p-0"
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <DateRangePanel
          initialFrom={seed.from}
          initialTo={seed.to}
          label={headerLabel}
          hint={emptyLabel}
          mode="range"
          disallowFuture={disallowFuture}
          maxRangeYears={maxRangeYears}
          locale={dateFnsLocale}
          onApply={(f, e) => {
            onChange(f, e)
            setOpen(false)
          }}
          onCancel={() => setOpen(false)}
        />
      </PopoverContent>
    </Popover>
  )
}

interface DateRangePanelProps {
  initialFrom: string
  initialTo: string
  /** Header of the panel ("Custom range"). */
  label: string
  /** Shown under the header while nothing is picked. */
  hint?: string
  /**
   * 'range': a one-sided pick mirrors into the other bound and an empty pair
   * clears the saved range. 'filter': either side may stay open ("from" /
   * "until"), but not both.
   */
  mode?: 'range' | 'filter'
  disallowFuture?: boolean
  maxRangeYears?: number
  locale: Locale
  /** Extra classes for the calendars (replaces the default height cap). */
  calendarsClassName?: string
  onApply: (from: string, to: string) => void
  onCancel: () => void
}

/**
 * The body of the range popover: the typed dates, the two calendars, the
 * checks (no future end, no range too wide) and Reset / Cancel / Apply. It
 * keeps its own drafts, so mount it when the popover opens.
 */
export function DateRangePanel({
  initialFrom,
  initialTo,
  label,
  hint,
  mode = 'range',
  disallowFuture = false,
  maxRangeYears,
  locale,
  calendarsClassName,
  onApply,
  onCancel,
}: DateRangePanelProps) {
  const { t } = useTranslation()
  const dateLocale = useDisplayLocale()
  const [draftFrom, setDraftFrom] = useState(initialFrom)
  const [draftTo, setDraftTo] = useState(initialTo)
  const [textValid, setTextValid] = useState(true)

  // Mirrors the Apply-time normalization (in 'range' mode a one-sided pick
  // mirrors into the other bound; a reversed pick swaps) so validation always
  // judges the same range Apply would actually commit.
  const normalized = (() => {
    const nextFrom = mode === 'range' ? draftFrom || draftTo : draftFrom
    const nextTo = mode === 'range' ? draftTo || draftFrom : draftTo
    return nextFrom && nextTo && nextFrom > nextTo
      ? { from: nextTo, to: nextFrom }
      : { from: nextFrom, to: nextTo }
  })()

  // An empty draft is always valid in 'range' mode: it is how Apply clears a saved range.
  const draftError = (() => {
    const { from: start, to: end } = normalized
    if (!start && !end) return null
    if (disallowFuture && end > localDateString()) {
      return t('transactions.filtersBar.futureDateError')
    }
    if (maxRangeYears && start && end) {
      // Compare against the calendar-year anniversary of the start date,
      // not a fixed day count — a day count would let leap days inside the
      // window push the effective cap past the intended N years.
      const maxEnd = addYears(new Date(start + 'T00:00:00'), maxRangeYears)
      if (new Date(end + 'T00:00:00') > maxEnd) {
        return t('transactions.filtersBar.rangeTooWideError', { years: maxRangeYears })
      }
    }
    return null
  })()

  const canApply = textValid && !draftError && (mode === 'range' || !!draftFrom || !!draftTo)
  const apply = () => {
    if (canApply) onApply(normalized.from, normalized.to)
  }

  return (
    <>
      <div className="border-b border-border/70 px-4 py-3">
        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
          {label}
        </p>
        <p className="mt-0.5 text-[11px] text-muted-foreground/70">
          {draftFrom || draftTo ? formatDateRange(draftFrom, draftTo, dateLocale) : hint}
        </p>
      </div>
      <div className="border-b border-border/70 px-4 py-3">
        <DateRangeFields
          from={draftFrom}
          to={draftTo}
          onChange={(f, e) => {
            setDraftFrom(f)
            setDraftTo(e)
          }}
          onValidityChange={setTextValid}
          onSubmit={apply}
          allowReversed={mode === 'range'}
        />
      </div>
      <DateRangeCalendars
        from={draftFrom}
        to={draftTo}
        onFromChange={setDraftFrom}
        onToChange={setDraftTo}
        locale={locale}
        className={calendarsClassName ?? 'max-h-[60vh] overflow-y-auto sm:max-h-none sm:overflow-visible'}
      />
      {draftError && (
        <div className="flex items-center gap-1.5 px-3 pb-2 text-[11px] text-rose-600 dark:text-rose-400">
          <AlertCircle size={12} className="shrink-0" />
          <span>{draftError}</span>
        </div>
      )}
      <div className="flex items-center justify-between gap-2 border-t border-border/70 px-3 py-2">
        <button
          type="button"
          onClick={() => {
            setDraftFrom('')
            setDraftTo('')
          }}
          className="text-[12px] font-medium text-muted-foreground transition-colors hover:text-foreground"
        >
          {t('transactions.filtersBar.reset')}
        </button>
        <div className="flex items-center gap-2">
          <Button type="button" variant="outline" size="sm" onClick={onCancel}>
            {t('transactions.filtersBar.cancel')}
          </Button>
          <Button type="button" size="sm" disabled={!canApply} onClick={apply}>
            {t('transactions.filtersBar.apply')}
          </Button>
        </div>
      </div>
    </>
  )
}

interface DateRangeFieldsProps {
  /** ISO yyyy-mm-dd, or '' for an open side. */
  from: string
  to: string
  onChange: (from: string, to: string) => void
  /** Reports whether the pair can be applied (no bad text, "To" not before "From"). */
  onValidityChange?: (valid: boolean) => void
  /** Enter on a valid pair. Escape is left to the surrounding popover/menu. */
  onSubmit?: () => void
  /** A "To" before "From" is not an error: the host swaps the two on Apply. */
  allowReversed?: boolean
}

/**
 * Two typeable dates ("From" / "To") in the locale's format. Each calendar of
 * the host can drive `from`/`to` too: the text follows whatever ISO the host
 * passes, unless it is the value this component just emitted (so typing is
 * never reformatted under the cursor; the text is normalised on blur).
 */
export function DateRangeFields({
  from,
  to,
  onChange,
  onValidityChange,
  onSubmit,
  allowReversed = false,
}: DateRangeFieldsProps) {
  const { t } = useTranslation()
  const locale = useDateLocale()
  const id = useId()
  const [fromText, setFromText] = useState(() => formatDateInput(from, locale))
  const [toText, setToText] = useState(() => formatDateInput(to, locale))
  // ISO values the texts currently stand for. A prop that differs came from
  // outside (a calendar click, a reset): re-render the text from it.
  const [synced, setSynced] = useState({ from, to })
  if (from !== synced.from || to !== synced.to) {
    if (from !== synced.from) setFromText(formatDateInput(from, locale))
    if (to !== synced.to) setToText(formatDateInput(to, locale))
    setSynced({ from, to })
  }

  // Errors show once the person leaves the field, presses Enter, or the text looks finished.
  const [touched, setTouched] = useState({ from: false, to: false })
  const fromBad = fromText.trim() !== '' && parseDateInput(fromText, locale) === null
  const toBad = toText.trim() !== '' && parseDateInput(toText, locale) === null
  const reversed = !allowReversed && !fromBad && !toBad && !!from && !!to && to < from
  const valid = !fromBad && !toBad && !reversed

  useEffect(() => {
    onValidityChange?.(valid)
  }, [valid, onValidityChange])

  const type = (side: 'from' | 'to', text: string) => {
    const iso = parseDateInput(text, locale) ?? ''
    if (side === 'from') {
      setFromText(text)
      setSynced({ from: iso, to })
      onChange(iso, to)
    } else {
      setToText(text)
      setSynced({ from, to: iso })
      onChange(from, iso)
    }
  }

  const example = formatDateInput('2026-03-25', locale)
  const showFrom = fromBad && (touched.from || looksCompleteDate(fromText))
  const showTo = toBad && (touched.to || looksCompleteDate(toText))
  const error = showFrom || showTo ? t('transactions.filtersBar.invalidDate') : reversed ? t('transactions.filtersBar.rangeOrder') : null

  const field = (side: 'from' | 'to', text: string, bad: boolean) => (
    <div className="min-w-0">
      <label
        htmlFor={`${id}-${side}`}
        className="mb-1 block text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80"
      >
        {t(side === 'from' ? 'transactions.filtersBar.fromLabel' : 'transactions.filtersBar.toLabel')}
      </label>
      <input
        id={`${id}-${side}`}
        type="text"
        inputMode="numeric"
        autoComplete="off"
        value={text}
        placeholder={t('transactions.filtersBar.dateExample', { example })}
        aria-invalid={(bad && (side === 'from' ? showFrom : showTo)) || (reversed && side === 'to') || undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        onChange={(event) => type(side, event.target.value)}
        onBlur={() => {
          setTouched((x) => ({ ...x, [side]: true }))
          const iso = side === 'from' ? from : to
          if (iso) (side === 'from' ? setFromText : setToText)(formatDateInput(iso, locale))
        }}
        onKeyDown={(event) => {
          // Keep typeahead of a surrounding menu out of the field; Escape still bubbles.
          if (event.key !== 'Escape') event.stopPropagation()
          if (event.key === 'Enter') setTouched((x) => ({ ...x, [side]: true }))
          if (event.key === 'Enter' && valid && (from || to)) {
            event.preventDefault()
            onSubmit?.()
          }
        }}
        className={cn(
          'h-9 w-full rounded-md border bg-card px-2 text-[13px] text-foreground outline-none focus:border-primary/60',
          (bad && (side === 'from' ? showFrom : showTo)) || (reversed && side === 'to') ? 'border-destructive' : 'border-border',
        )}
      />
    </div>
  )

  return (
    <div>
      <div className="grid grid-cols-2 gap-2">
        {field('from', fromText, fromBad)}
        {field('to', toText, toBad)}
      </div>
      {error && (
        <p id={`${id}-error`} role="alert" className="mt-1.5 text-[11px] text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}

interface DateRangeCalendarsProps {
  from: string
  to: string
  onFromChange: (value: string) => void
  onToChange: (value: string) => void
  locale: Locale
  className?: string
  /** Show only this calendar (phones: one at a time). Both when omitted. */
  only?: 'from' | 'to'
}

/** The two calendars of a custom range ("From" and "To"), side by side on a
 *  wide screen and stacked on a phone. Shared by the picker's panel and the
 *  Transactions filter on a phone. Each calendar jumps to its date when the
 *  date changes from outside (typed in, or reset). */
export function DateRangeCalendars({ from, to, onFromChange, onToChange, locale, className, only }: DateRangeCalendarsProps) {
  const { t } = useTranslation()
  return (
    <div className={cn('flex flex-col gap-4 p-3 sm:flex-row sm:gap-0', className)}>
      {only !== 'to' && <div className="sm:border-r sm:border-border/60 sm:pr-2">
        <p className="px-2 pb-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80">
          {t('transactions.filtersBar.fromLabel')}
        </p>
        <Calendar
          key={from}
          selected={from ? new Date(from + 'T00:00:00') : undefined}
          defaultMonth={from ? new Date(from + 'T00:00:00') : new Date()}
          locale={locale}
          onSelect={(d) => onFromChange(d ? localDateString(d) : '')}
        />
      </div>}
      {only !== 'from' && <div className="sm:pl-2">
        <p className="px-2 pb-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80">
          {t('transactions.filtersBar.toLabel')}
        </p>
        <Calendar
          key={to}
          selected={to ? new Date(to + 'T00:00:00') : undefined}
          defaultMonth={to ? new Date(to + 'T00:00:00') : from ? new Date(from + 'T00:00:00') : new Date()}
          locale={locale}
          onSelect={(d) => onToChange(d ? localDateString(d) : '')}
        />
      </div>}
    </div>
  )
}
