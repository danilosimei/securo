import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CategoryGlyph } from './category-glyph'

describe('CategoryGlyph', () => {
  it('draws the named icon in the color, with no background', () => {
    const { container } = render(<CategoryGlyph icon="shopping-cart" color="#2a78d6" />)
    const svg = container.querySelector('svg')!
    expect(svg).toHaveClass('lucide-shopping-cart')
    expect(svg).toHaveStyle({ color: '#2a78d6' })
    expect(svg.getAttribute('width')).toBe('14')
    expect(container.querySelector('[style*="background"]')).toBeNull()
  })

  it('takes a size', () => {
    const { container } = render(<CategoryGlyph icon="house" color="#000000" size={18} />)
    expect(container.querySelector('svg')!.getAttribute('width')).toBe('18')
  })

  it('shows an emoji icon as text', () => {
    render(<CategoryGlyph icon="🍔" color="#f97316" />)
    expect(screen.getByText('🍔')).toBeInTheDocument()
  })

  it('falls back to a placeholder for an unknown or missing icon', () => {
    const { container } = render(<CategoryGlyph icon={null} color={null} />)
    expect(container.querySelector('svg')).toHaveClass('lucide-circle-question-mark')
  })
})
