import { useSearchParams } from 'react-router-dom'

/**
 * The active tab of a page, kept in `?tab=` so a link, a reload or Back lands
 * on it. The first value is the default and stays out of the URL; an unknown
 * value falls back to it. `replace`, so Back leaves the page rather than
 * walking through the tabs somebody clicked on the way.
 */
export function useUrlTab<T extends string>(
  values: readonly T[],
  key = 'tab',
): [T, (next: T) => void] {
  const [searchParams, setSearchParams] = useSearchParams()
  const requested = searchParams.get(key)
  const tab = values.find((value) => value === requested) ?? values[0]
  const setTab = (next: T) =>
    setSearchParams(
      (previous) => {
        const params = new URLSearchParams(previous)
        if (next === values[0]) params.delete(key)
        else params.set(key, next)
        return params
      },
      { replace: true },
    )
  return [tab, setTab]
}
