import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'

import { currentMonth } from '@/lib/month-utils'
import { rememberMonth, useMonthParam } from './use-month-param'

function setup(route: string) {
  return renderHook(
    () => ({ month: useMonthParam(), location: useLocation() }),
    { wrapper: ({ children }) => <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter> },
  )
}

describe('useMonthParam', () => {
  it('reads the month from the URL', () => {
    expect(setup('/x?month=2026-03').result.current.month[0]).toBe('2026-03')
  })

  it('falls back to the month last chosen, then to the current month', () => {
    expect(setup('/x').result.current.month[0]).toBe(currentMonth())
    rememberMonth('2026-02')
    expect(setup('/x').result.current.month[0]).toBe('2026-02')
    expect(setup('/x?month=nope').result.current.month[0]).toBe('2026-02')
  })

  it('writes the chosen month to the URL, keeps other params and remembers it', () => {
    const { result } = setup('/x?q=a')
    act(() => result.current.month[1]('2026-05'))
    expect(result.current.location.search).toBe('?q=a&month=2026-05')
    expect(setup('/y').result.current.month[0]).toBe('2026-05')
  })

  it('remembers a month that arrived in the URL', () => {
    setup('/x?month=2026-07')
    expect(setup('/y').result.current.month[0]).toBe('2026-07')
  })
})
