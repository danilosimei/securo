/** "Ask AI": a conversation about one store, in a big dialog (a full-screen
 *  sheet on a phone). The person explains, the AI answers in a line or two
 *  and, when it can, proposes a category in a card; Accept creates what is
 *  new, files the store's rows as the person's choice, saves the store's
 *  note and closes. Enter sends, Shift+Enter breaks the line.
 *
 *  The conversation is the provider's (it survives closing the dialog, not a
 *  reload); this component only draws it and sends the next message. */
import { useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, SendHorizontal, Sparkles } from 'lucide-react'
import {
  categories as categoriesApi,
  categoryGroups as categoryGroupsApi,
  categorization as categorizationApi,
} from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateCategoryQueries } from '@/lib/invalidate-queries'
import {
  ASK_HALTING_ERRORS,
  ASK_MAX_PERSON_MESSAGES,
  askErrorKey,
  personCount,
  toWire,
  type AskBlocked,
  type ChatTurn,
  type Conversation,
} from '@/lib/categorization-ask'
import { CATEGORIZATION_SUMMARY_KEY } from '@/hooks/use-categorization-summary'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatCurrency } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { AskSuggestionCard, type AskPick } from '@/components/categorization/ask-suggestion-card'
import type { AskTarget } from '@/components/categorization/ask-provider'
import type { ChooseRequest, ChooseResult } from '@/types'

interface AskDialogProps {
  target: AskTarget
  open: boolean
  onOpenChange: (open: boolean) => void
  conversation: Conversation
  /** Why nothing more can be sent (the page's reason; null: nothing). */
  blocked: AskBlocked | null
  /** Changes the conversation of generation `gen` (a stale one: no-op). */
  onChange: (gen: number, change: (current: Conversation) => Partial<Omit<Conversation, 'gen'>>) => void
  /** The choice went through: the provider closes and tells the row. */
  onAccepted: (result: ChooseResult) => void
}

export function AskDialog({
  target,
  open,
  onOpenChange,
  conversation,
  blocked,
  onChange,
  onAccepted,
}: AskDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()

  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [accepting, setAccepting] = useState(false)
  // An answer said sending again cannot work (cap, AI off, no connection).
  const [halted, setHalted] = useState(false)
  // Reopening the same conversation tries again: what stopped it may be
  // over (the page's own `blocked` still says when it is not).
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setHalted(false)
      setError(null)
    }
  }
  const { turns, thinking, gen } = conversation
  // The generation now: an answer for an older one changes nothing here.
  const genRef = useRef(gen)
  useLayoutEffect(() => {
    genRef.current = gen
  })
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const logRef = useRef<HTMLDivElement>(null)

  // Same keys as the rest of the app, so the cache is shared.
  const categoriesQuery = useQuery({ queryKey: ['categories'], queryFn: categoriesApi.list, enabled: open })
  const groupsQuery = useQuery({ queryKey: ['categoryGroups'], queryFn: categoryGroupsApi.list, enabled: open })
  const categories = categoriesQuery.data ?? []
  const groups = groupsQuery.data ?? []

  const used = personCount(turns)
  const limitReached = used >= ASK_MAX_PERSON_MESSAGES
  const stopped = limitReached || halted || (!!blocked && blocked !== 'readOnly')
  const canSend = !thinking && !accepting && !stopped && draft.trim().length > 0
  // What the field is waiting for, read out as it changes.
  const status = limitReached
    ? t('categorization.ask.limitReached', { max: ASK_MAX_PERSON_MESSAGES })
    : blocked && !halted
      ? t(`categorization.ask.blocked.${blocked}`)
      : null

  // The newest turn in sight.
  useLayoutEffect(() => {
    const log = logRef.current
    if (log) log.scrollTop = log.scrollHeight
  }, [turns.length, thinking])

  const send = async () => {
    const text = draft.trim()
    if (!canSend || !text) return
    const mine: ChatTurn = { role: 'user', text, wire: text }
    const sent = [...turns, mine]
    const at = gen
    onChange(at, () => ({ turns: sent, thinking: true }))
    setDraft('')
    setError(null)
    try {
      const answer = await categorizationApi.ask({
        merchant_key: target.merchantKey,
        ...(target.transactionIds ? { transaction_ids: target.transactionIds } : {}),
        messages: toWire(sent),
      })
      onChange(at, (c) => ({
        turns: [...c.turns, { role: 'assistant', text: answer.reply, wire: answer.assistant_turn, answer }],
        thinking: false,
      }))
    } catch (err) {
      // The message did not go: it leaves the conversation and goes back to
      // the field, so sending again is one key away.
      onChange(at, (c) => ({ turns: c.turns.filter((turn) => turn !== mine), thinking: false }))
      if (genRef.current === at) {
        const key = askErrorKey(err)
        setDraft((current) => current || text)
        setError(t(key))
        if (ASK_HALTING_ERRORS.has(key)) setHalted(true)
      }
    } finally {
      // Each message counts in the month's spend: the cap may be reached.
      void queryClient.invalidateQueries({ queryKey: [...CATEGORIZATION_SUMMARY_KEY] })
    }
  }

  const accept = async (pick: AskPick) => {
    if (accepting || thinking) return
    setAccepting(true)
    setError(null)
    // Each answer's note is the store's whole note as of that answer, so the
    // latest one wins, whichever card is accepted (an older card's note would
    // undo what later answers added); none at all means "keep it".
    const note = latestNote(turns)
    const payload: ChooseRequest = {
      merchant_key: target.merchantKey,
      ...(target.transactionIds ? { transaction_ids: target.transactionIds } : {}),
      ...pick,
      ...(note != null ? { note } : {}),
    }
    try {
      const result = await categorizationApi.choose(payload)
      // The one refresh of the categories for this answer (the rows do not repeat it).
      if (result.created_category || result.created_group) invalidateCategoryQueries(queryClient)
      onAccepted(result)
    } catch (err) {
      setError(extractApiError(err, t('common.error')))
    } finally {
      setAccepting(false)
    }
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== 'Enter' || e.shiftKey || e.nativeEvent.isComposing) return
    e.preventDefault()
    void send()
  }

  const showDate = (iso: string) =>
    new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString(dateLocale)
  const total = mask(formatCurrency(target.total, target.currency, locale))
  const context = target.lastDate
    ? t('categorization.ask.context', { count: target.count, total, date: showDate(target.lastDate) })
    : t('categorization.ask.contextNoDate', { count: target.count, total })
  const samples = target.samples.filter(Boolean).slice(0, 3)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          'flex flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl h-[min(80vh,44rem)]',
          // A phone gets the whole screen: the keyboard takes half of it.
          'max-sm:inset-0 max-sm:top-0 max-sm:left-0 max-sm:h-dvh max-sm:max-w-none max-sm:translate-x-0 max-sm:translate-y-0 max-sm:rounded-none max-sm:border-0',
        )}
        onOpenAutoFocus={(e) => {
          if (!inputRef.current || inputRef.current.disabled) return
          e.preventDefault()
          inputRef.current.focus()
        }}
        onCloseAutoFocus={(e) => {
          if (!target.restoreFocus) return
          e.preventDefault()
          target.restoreFocus()
        }}
      >
        <DialogHeader className="shrink-0 gap-1 border-b border-border px-4 py-3 pr-12 text-left sm:px-5">
          <p className="flex items-center gap-1.5 text-xs font-medium text-primary">
            <Sparkles size={13} aria-hidden="true" />
            {t('categorization.ask.button')}
          </p>
          <DialogTitle className="truncate text-base">
            {t('categorization.ask.title', { name: target.storeName })}
          </DialogTitle>
          <DialogDescription className="text-xs tabular-nums">{context}</DialogDescription>
          {samples.length > 0 && (
            <ul aria-label={t('categorization.ask.samples')} className="mt-1 flex flex-wrap gap-1.5">
              {samples.map((sample) => (
                <li
                  key={sample}
                  title={sample}
                  className="max-w-full truncate rounded-md bg-muted px-2 py-0.5 font-mono text-[11px] text-muted-foreground sm:max-w-[16rem]"
                >
                  {sample}
                </li>
              ))}
            </ul>
          )}
        </DialogHeader>

        <div
          ref={logRef}
          role="log"
          aria-live="polite"
          aria-label={t('categorization.ask.conversation')}
          className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4 sm:px-5"
        >
          {turns.length === 0 && !thinking && (
            <p className="mx-auto max-w-md py-6 text-center text-sm text-muted-foreground">
              {t('categorization.ask.intro')}
            </p>
          )}
          {turns.map((turn, i) => (
            <Turn key={i} turn={turn}>
              {turn.answer?.suggestion && (
                <AskSuggestionCard
                  suggestion={turn.answer.suggestion}
                  categories={categories}
                  groups={groups}
                  // While a message is on its way, its answer may change the
                  // proposal (and accepting would end the conversation).
                  busy={accepting || thinking}
                  suggestedGroupId={target.suggestedGroupId}
                  onPick={(pick) => void accept(pick)}
                />
              )}
            </Turn>
          ))}
          {thinking && (
            <div className="flex">
              <p className="inline-flex items-center gap-1.5 rounded-2xl rounded-bl-sm bg-muted px-3 py-2 text-sm text-muted-foreground">
                <Loader2 size={14} className="animate-spin" aria-hidden="true" />
                {t('categorization.ask.thinking')}
              </p>
            </div>
          )}
        </div>

        <div className="shrink-0 border-t border-border px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-5">
          {error && (
            <p role="alert" className="mb-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </p>
          )}
          <p role="status" aria-live="polite" className={cn('text-xs text-muted-foreground', status && 'mb-2')}>
            {status}
          </p>
          <div className="flex items-end gap-2">
            <textarea
              ref={inputRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={stopped}
              rows={2}
              maxLength={4000}
              aria-label={t('categorization.ask.message')}
              placeholder={t('categorization.ask.placeholder')}
              className="max-h-40 min-h-[2.75rem] flex-1 resize-none rounded-lg border border-input bg-transparent px-3 py-2 text-base outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 sm:text-sm"
            />
            <Button
              type="button"
              size="icon"
              className="size-11 shrink-0"
              disabled={!canSend}
              aria-label={t('categorization.ask.send')}
              title={t('categorization.ask.send')}
              onClick={() => void send()}
            >
              {thinking ? <Loader2 size={16} className="animate-spin" /> : <SendHorizontal size={16} />}
            </Button>
          </div>
          <div className="mt-1.5 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
            <span className="hidden sm:inline">{t('categorization.ask.keys')}</span>
            <span className="ml-auto tabular-nums">
              {t('categorization.ask.counter', { used, max: ASK_MAX_PERSON_MESSAGES })}
            </span>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

/** The note of the newest answer that has one (null: none has). */
function latestNote(turns: ChatTurn[]): string | null {
  for (let i = turns.length - 1; i >= 0; i--) {
    const note = turns[i].answer?.note
    if (note != null) return note
  }
  return null
}

function Turn({ turn, children }: { turn: ChatTurn; children?: React.ReactNode }) {
  const { t } = useTranslation()
  const mine = turn.role === 'user'
  return (
    <div className={cn('flex', mine ? 'justify-end' : 'justify-start')}>
      <div className={cn('min-w-0', mine ? 'max-w-[85%]' : 'w-full max-w-[min(100%,34rem)]')}>
        <span className="sr-only">{mine ? t('categorization.ask.you') : t('categorization.ask.ai')}: </span>
        <p
          className={cn(
            'inline-block whitespace-pre-wrap break-words rounded-2xl px-3 py-2 text-sm',
            mine
              ? 'rounded-br-sm bg-primary text-primary-foreground'
              : 'rounded-bl-sm bg-muted text-foreground',
          )}
        >
          {turn.text}
        </p>
        {children}
      </div>
    </div>
  )
}
