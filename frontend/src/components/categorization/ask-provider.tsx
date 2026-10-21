/** Holds the "Ask AI" dialog for the Categorization page: any row (Review or
 *  Applied) opens it about its store with `useAsk().open(target)`, and
 *  `blocked` says why it cannot (the entry points show it disabled).
 *
 *  The conversations live here, by store, for as long as the page: reopening
 *  a store resumes its conversation, a reload starts over, and an accepted
 *  one is gone (its store was answered). */
import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import { useWorkspace } from '@/contexts/workspace-context'
import { useCategorizationSummary } from '@/hooks/use-categorization-summary'
import { askBlocked, type AskBlocked, type ChatTurn } from '@/lib/categorization-ask'
import { AskDialog } from '@/components/categorization/ask-dialog'
import type { ChooseResult } from '@/types'

/** The store a conversation is about, and what its row does with the answer. */
export interface AskTarget {
  merchantKey: string
  storeName: string
  count: number
  /** Unmasked, in `currency`: the dialog masks it. */
  total: number
  currency: string
  lastDate: string | null
  samples: string[]
  /** Applied: exactly these rows. Review: left out (the store's pending rows). */
  transactionIds?: string[]
  /** The group "Another category" offers first. */
  suggestedGroupId: string | null
  /** The choice went through (the dialog is closing). */
  onAccepted: (result: ChooseResult) => void
  /** Where the focus goes when the dialog closes; without it, back to the
   *  element that opened it. */
  restoreFocus?: () => void
}

interface AskContextValue {
  open: (target: AskTarget) => void
  blocked: AskBlocked | null
  /** False outside an `AskProvider`: the entry points are left out. */
  available: boolean
}

const AskContext = createContext<AskContextValue | null>(null)
const OUTSIDE: AskContextValue = { open: () => {}, blocked: null, available: false }

// The hook sits with its provider, as the other contexts here do.
// eslint-disable-next-line react-refresh/only-export-components
export function useAsk(): AskContextValue {
  return useContext(AskContext) ?? OUTSIDE
}

export function AskProvider({ children }: { children: React.ReactNode }) {
  const { canWrite } = useWorkspace()
  const { data: summary } = useCategorizationSummary()
  const blocked = askBlocked(summary, canWrite)

  const [target, setTarget] = useState<AskTarget | null>(null)
  const [isOpen, setIsOpen] = useState(false)
  const [chats, setChats] = useState<Map<string, ChatTurn[]>>(() => new Map())
  const [thinking, setThinking] = useState<Set<string>>(() => new Set())

  const open = useCallback((next: AskTarget) => {
    setTarget(next)
    setIsOpen(true)
  }, [])
  const value = useMemo(() => ({ open, blocked, available: true }), [open, blocked])

  const key = target?.merchantKey ?? ''
  const onTurns = useCallback(
    (update: (turns: ChatTurn[]) => ChatTurn[]) =>
      setChats((prev) => new Map(prev).set(key, update(prev.get(key) ?? []))),
    [key],
  )
  const onThinking = useCallback(
    (on: boolean) =>
      setThinking((prev) => {
        const next = new Set(prev)
        if (on) next.add(key)
        else next.delete(key)
        return next
      }),
    [key],
  )
  const onAccepted = useCallback(
    (result: ChooseResult) => {
      if (!target) return
      setChats((prev) => {
        const next = new Map(prev)
        next.delete(target.merchantKey)
        return next
      })
      setIsOpen(false)
      target.onAccepted(result)
    },
    [target],
  )

  return (
    <AskContext.Provider value={value}>
      {children}
      {target && (
        <AskDialog
          key={target.merchantKey}
          target={target}
          open={isOpen}
          onOpenChange={setIsOpen}
          turns={chats.get(key) ?? []}
          thinking={thinking.has(key)}
          onTurns={onTurns}
          onThinking={onThinking}
          onAccepted={onAccepted}
        />
      )}
    </AskContext.Provider>
  )
}
