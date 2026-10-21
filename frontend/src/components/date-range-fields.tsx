import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useDateLocale } from '@/hooks/use-display-locale'
import { formatDateInput, looksCompleteDate, parseDateInput } from '@/lib/date-input'
import { cn } from '@/lib/utils'

interface DateRangeFieldsProps {
  /** ISO yyyy-mm-dd, or '' for an open side. */
  from: string
  to: string
  onChange: (from: string, to: string) => void
  /** Reports whether the pair can be applied (no bad text, "To" not before "From"). */
  onValidityChange?: (valid: boolean) => void
  /** Enter on a valid pair. Escape is left to the surrounding popover/menu. */
  onSubmit?: () => void
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
  const reversed = !fromBad && !toBad && !!from && !!to && to < from
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
