import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

function readRemembered(storageKey: string): string[] {
  try {
    const stored = localStorage.getItem(storageKey)
    return stored ? stored.split(',').filter(Boolean) : []
  } catch {
    return []
  }
}

function remember(storageKey: string, ids: string[]) {
  try {
    if (ids.length) localStorage.setItem(storageKey, ids.join(','))
    else localStorage.removeItem(storageKey)
  } catch {
    // Private mode or blocked storage: the URL still carries the choice.
  }
}

/**
 * The ids a page leaves out, kept in `?hide=a,b` so a link, a reload or Back
 * brings them back, and remembered in this browser for the next visit. It
 * holds what is off rather than what is on, so anything new shows up on.
 * Without `?hide=`, the last choice made here. `replace`, so toggling does
 * not fill the history.
 */
export function useHiddenParam(storageKey: string, key = 'hide'): [Set<string>, (next: Iterable<string>) => void] {
  const [searchParams, setSearchParams] = useSearchParams()
  const requested = searchParams.get(key)
  const [remembered, setRemembered] = useState(() => readRemembered(storageKey))

  useEffect(() => {
    if (requested !== null) remember(storageKey, requested.split(',').filter(Boolean))
  }, [requested, storageKey])

  const hidden = useMemo(
    () => new Set(requested !== null ? requested.split(',').filter(Boolean) : remembered),
    [requested, remembered],
  )

  const setHidden = (next: Iterable<string>) => {
    const ids = [...new Set(next)]
    remember(storageKey, ids)
    setRemembered(ids)
    setSearchParams(
      (previous) => {
        const params = new URLSearchParams(previous)
        if (ids.length) params.set(key, ids.join(','))
        else params.delete(key)
        return params
      },
      { replace: true },
    )
  }
  return [hidden, setHidden]
}
