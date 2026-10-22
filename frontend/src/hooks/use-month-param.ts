import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'

import { currentMonth } from '@/lib/month-utils'

const STORAGE_KEY = 'securo.month'
const MONTH_REGEX = /^\d{4}-(0[1-9]|1[0-2])$/

/** The month last chosen on any page, for the rest of this browser tab. */
export function readRememberedMonth(): string | null {
  try {
    const stored = sessionStorage.getItem(STORAGE_KEY)
    return stored && MONTH_REGEX.test(stored) ? stored : null
  } catch {
    return null
  }
}

export function rememberMonth(month: string) {
  try {
    sessionStorage.setItem(STORAGE_KEY, month)
  } catch {
    // Private mode or blocked storage: the URL still carries the month.
  }
}

/**
 * The month a page shows, kept in `?month=YYYY-MM` so a link, a reload or Back
 * lands on it. Without `?month=`, the month last chosen on Dashboard, Budgets
 * or Transactions (so the three stay on the same month), else the current one.
 * `replace`, so stepping through months does not fill the history.
 */
export function useMonthParam(): [string, (next: string) => void] {
  const [searchParams, setSearchParams] = useSearchParams()
  const requested = searchParams.get('month')
  const fromUrl = requested && MONTH_REGEX.test(requested) ? requested : null
  const month = fromUrl ?? readRememberedMonth() ?? currentMonth()

  useEffect(() => {
    if (fromUrl) rememberMonth(fromUrl)
  }, [fromUrl])

  const setMonth = (next: string) => {
    rememberMonth(next)
    setSearchParams(
      (previous) => {
        const params = new URLSearchParams(previous)
        params.set('month', next)
        return params
      },
      { replace: true },
    )
  }
  return [month, setMonth]
}
