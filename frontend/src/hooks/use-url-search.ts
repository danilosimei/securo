import { useSearchParams } from 'react-router-dom'

/**
 * The text of a page's search box, kept in `?q=` so a link, a reload or Back
 * brings it back. Other params stay as they are; an empty search leaves the
 * URL. `replace`, so typing does not fill the history.
 */
export function useUrlSearch(key = 'q'): [string, (next: string) => void] {
  const [searchParams, setSearchParams] = useSearchParams()
  const value = searchParams.get(key) ?? ''
  const setValue = (next: string) =>
    setSearchParams(
      (previous) => {
        const params = new URLSearchParams(previous)
        if (next) params.set(key, next)
        else params.delete(key)
        return params
      },
      { replace: true },
    )
  return [value, setValue]
}
