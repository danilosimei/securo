import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import { CategorizationOriginBadge } from '@/components/categorization-origin-badge'
import type { CategorySource } from '@/types'
import { renderWithProviders, t } from '@/test/utils'

describe('CategorizationOriginBadge', () => {
  it.each([
    ['ai', 'categorization.origin.ai'],
    ['memory', 'categorization.origin.memory'],
    ['rule', 'categorization.origin.rule'],
  ] as const)('shows the %s label', (source, key) => {
    renderWithProviders(<CategorizationOriginBadge source={source} />)
    expect(screen.getByText(t(key))).toBeInTheDocument()
  })

  it.each(['user', 'legacy', 'none', 'provider', 'import', 'recurring', 'installment'] as CategorySource[])(
    'renders nothing for %s',
    (source) => {
      const { container } = renderWithProviders(<CategorizationOriginBadge source={source} />)
      expect(container).toBeEmptyDOMElement()
    },
  )

  it('renders nothing without a source', () => {
    const { container } = renderWithProviders(<CategorizationOriginBadge />)
    expect(container).toBeEmptyDOMElement()
  })

  it('pending takes precedence and links to the review tab', () => {
    renderWithProviders(<CategorizationOriginBadge source="ai" pending />)
    const link = screen.getByRole('link', { name: t('categorization.origin.reviewAria') })
    expect(link).toHaveAttribute('href', '/categorization?tab=review')
    expect(screen.queryByText(t('categorization.origin.ai'))).not.toBeInTheDocument()
  })

  it('pending shows even when the source would render nothing', () => {
    renderWithProviders(<CategorizationOriginBadge source="none" pending />)
    expect(screen.getByRole('link')).toBeInTheDocument()
  })

  it('uses the reason as the tooltip when given', () => {
    renderWithProviders(<CategorizationOriginBadge source="ai" reason="parece mercado" />)
    expect(screen.getByText(t('categorization.origin.ai')).closest('[title]')).toHaveAttribute('title', 'parece mercado')
  })

  it('falls back to the origin explanation as tooltip', () => {
    renderWithProviders(<CategorizationOriginBadge source="memory" />)
    expect(screen.getByText(t('categorization.origin.memory')).closest('[title]')).toHaveAttribute(
      'title',
      t('categorization.origin.memoryTooltip'),
    )
  })

  it('compact drops the text but keeps an accessible name', () => {
    renderWithProviders(<CategorizationOriginBadge source="rule" compact />)
    expect(screen.queryByText(t('categorization.origin.rule'))).not.toBeInTheDocument()
    expect(screen.getByLabelText(t('categorization.origin.rule'))).toBeInTheDocument()
  })
})
