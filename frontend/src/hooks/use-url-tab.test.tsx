import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'

import { useUrlTab } from './use-url-tab'

const TABS = ['one', 'two'] as const

function setup(route: string) {
  return renderHook(
    () => ({ tab: useUrlTab(TABS), location: useLocation() }),
    { wrapper: ({ children }) => <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter> },
  )
}

describe('useUrlTab', () => {
  it('reads the tab from the URL and falls back to the first value', () => {
    expect(setup('/x?tab=two').result.current.tab[0]).toBe('two')
    expect(setup('/x?tab=nope').result.current.tab[0]).toBe('one')
    expect(setup('/x').result.current.tab[0]).toBe('one')
  })

  it('keeps the default out of the URL and other params in it', () => {
    const { result } = setup('/x?q=a')
    act(() => result.current.tab[1]('two'))
    expect(result.current.location.search).toBe('?q=a&tab=two')
    act(() => result.current.tab[1]('one'))
    expect(result.current.location.search).toBe('?q=a')
  })
})
