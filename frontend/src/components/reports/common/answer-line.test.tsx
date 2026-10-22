import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Wallet } from 'lucide-react'
import type { TFunction } from 'i18next'

import { AnswerLine } from '@/components/reports/common/answer-line'
import { answerParts, answerText } from '@/components/reports/common/answer-parts'
import { i18n } from '@/test/utils'

const tPt = i18n.getFixedT('pt-BR') as unknown as TFunction

describe('answer sentences', () => {
  it('cuts the translated sentence at its placeholders, in the language word order', () => {
    const parts = answerParts(tPt, 'reports.overview.headline.monthUp', {
      month: 'setembro',
      spending: { kind: 'value', text: 'R$ 75,8 mil' },
      change: { kind: 'delta', change: 0.86, good: false, locale: 'pt-BR' },
      previous: 'agosto',
    })
    expect(parts.slice(0, 4)).toEqual(['Em ', 'setembro', ' você gastou ', { kind: 'value', text: 'R$ 75,8 mil' }])
    expect(answerText(parts)).toBe('Em setembro você gastou R$ 75,8 mil, 86% a mais que em agosto')
  })

  it('draws the numbers bold, the change as a pill and the names as links', () => {
    render(
      <MemoryRouter>
        <AnswerLine
          icon={Wallet}
          parts={[
            'gastou ',
            { kind: 'value', text: 'R$ 10' },
            ', ',
            { kind: 'delta', change: -0.2, good: true, locale: 'pt-BR' },
            ' em ',
            { kind: 'link', text: 'Casa', to: '/reports?tab=explore&group=g1' },
          ]}
        />
      </MemoryRouter>,
    )
    const line = screen.getByTestId('report-headline')
    // The first letter goes up.
    expect(line).toHaveTextContent('Gastou R$ 10, ▼20% em Casa')
    expect(within(line).getByText('R$ 10')).toHaveClass('font-semibold')
    expect(within(line).getByTestId('delta-pill')).toHaveAttribute('data-tone', 'good')
    expect(within(line).getByRole('link', { name: 'Casa' })).toHaveAttribute('href', '/reports?tab=explore&group=g1')
  })

  it('shows a skeleton while loading and nothing without a sentence', () => {
    const { container, rerender } = render(<AnswerLine icon={Wallet} parts={null} loading />)
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull()
    rerender(<AnswerLine icon={Wallet} parts={null} />)
    expect(container).toBeEmptyDOMElement()
  })
})
