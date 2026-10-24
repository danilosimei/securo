import { describe, expect, it } from 'vitest'

import { FadePanel } from '@/components/ui/fade-panel'
import { renderWithProviders } from '@/test/utils'

describe('FadePanel', () => {
  it('mounts a new panel, which fades in, each time the key changes', () => {
    const { container, rerender } = renderWithProviders(<FadePanel panelKey="a">A</FadePanel>)
    const first = container.querySelector('[data-slot="fade-panel"]')!
    expect(first).toHaveClass('motion-safe:animate-in', 'motion-safe:fade-in-0')

    rerender(<FadePanel panelKey="a">A again</FadePanel>)
    expect(container.querySelector('[data-slot="fade-panel"]')).toBe(first)

    rerender(<FadePanel panelKey="b">B</FadePanel>)
    const second = container.querySelector('[data-slot="fade-panel"]')!
    expect(second).not.toBe(first)
    expect(second).toHaveTextContent('B')
  })

  it('keeps the caller classes', () => {
    const { container } = renderWithProviders(<FadePanel panelKey="a" className="mt-4">A</FadePanel>)

    expect(container.querySelector('[data-slot="fade-panel"]')).toHaveClass('mt-4')
  })
})
