import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { Scoreboard } from '@/components/categorization/scoreboard'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({ categorization: { scoreboard: vi.fn() } }))

vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('Scoreboard', () => {
  it('decididas em %, para revisar em número, sem aguardando quando é zero', async () => {
    api.categorization.scoreboard.mockResolvedValue({ total: 3693, decided: 3681, to_review: 12, waiting: 0 })
    renderWithProviders(<Scoreboard />)
    const line = await screen.findByTestId('categorization-scoreboard')
    expect(line).toHaveTextContent('3,693 transactions · 99% decided · 12 to review')
    expect(line).not.toHaveTextContent(/waiting/i)
  })

  it('aguardando a IA aparece, em número, só quando há alguma', async () => {
    api.categorization.scoreboard.mockResolvedValue({ total: 3693, decided: 3678, to_review: 12, waiting: 3 })
    renderWithProviders(<Scoreboard />)
    const line = await screen.findByTestId('categorization-scoreboard')
    expect(line).toHaveTextContent('3,693 transactions · 99% decided · 12 to review · 3 waiting for the AI')
  })

  it('nada para revisar: tudo revisado', async () => {
    api.categorization.scoreboard.mockResolvedValue({ total: 120, decided: 120, to_review: 0, waiting: 0 })
    renderWithProviders(<Scoreboard />)
    const line = await screen.findByTestId('categorization-scoreboard')
    expect(line).toHaveTextContent('120 transactions · 100% decided · all reviewed')
  })

  it('a barra mostra a fatia das decididas e uma lasca visível para revisar, mesmo abaixo de 1%', async () => {
    api.categorization.scoreboard.mockResolvedValue({ total: 3693, decided: 3690, to_review: 3, waiting: 0 })
    renderWithProviders(<Scoreboard />)
    await screen.findByTestId('categorization-scoreboard')
    const review = document.querySelector('[data-bar="review"]') as HTMLElement
    expect(review).not.toBeNull()
    expect(review.style.minWidth).not.toBe('')
    expect(document.querySelector('[data-bar="decided"]')).not.toBeNull()
  })

  it('o (i) tem nome curto, abre ao tocar e fecha ao tocar de novo', async () => {
    api.categorization.scoreboard.mockResolvedValue({ total: 4, decided: 3, to_review: 1, waiting: 0 })
    const { user } = renderWithProviders(<Scoreboard />)
    const about = await screen.findByRole('button', { name: t('categorization.scoreboard.about') })
    await user.click(about)
    expect(await screen.findByRole('tooltip')).toHaveTextContent(t('categorization.scoreboard.excluded'))
    await user.click(about)
    await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument())
  })

  it('sem nada na base não mostra nada', async () => {
    api.categorization.scoreboard.mockResolvedValue({ total: 0, decided: 0, to_review: 0, waiting: 0 })
    renderWithProviders(<Scoreboard />)
    await waitFor(() => expect(api.categorization.scoreboard).toHaveBeenCalled())
    expect(screen.queryByTestId('categorization-scoreboard')).not.toBeInTheDocument()
  })
})
