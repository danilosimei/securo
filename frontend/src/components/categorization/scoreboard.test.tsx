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
  it('mostra o total, as decididas e as para revisar, sem aguardando quando é zero', async () => {
    api.categorization.scoreboard.mockResolvedValue({ total: 120, decided: 90, to_review: 30, waiting: 0 })
    renderWithProviders(<Scoreboard />)
    const line = await screen.findByTestId('categorization-scoreboard')
    expect(line).toHaveTextContent('120 transactions · 75% decided · 25% to review')
    expect(line).not.toHaveTextContent(/waiting/i)
  })

  it('aguardando a IA aparece só quando há alguma', async () => {
    api.categorization.scoreboard.mockResolvedValue({ total: 1200, decided: 600, to_review: 300, waiting: 300 })
    renderWithProviders(<Scoreboard />)
    const line = await screen.findByTestId('categorization-scoreboard')
    expect(line).toHaveTextContent('1,200 transactions · 50% decided · 25% to review · 25% waiting for the AI')
  })

  it('explica o que fica de fora da base', async () => {
    api.categorization.scoreboard.mockResolvedValue({ total: 4, decided: 3, to_review: 1, waiting: 0 })
    const { user } = renderWithProviders(<Scoreboard />)
    const about = await screen.findByRole('button', { name: t('categorization.scoreboard.excluded') })
    await user.click(about)
    expect(await screen.findByRole('tooltip')).toHaveTextContent(t('categorization.scoreboard.excluded'))
  })

  it('sem nada na base não mostra nada', async () => {
    api.categorization.scoreboard.mockResolvedValue({ total: 0, decided: 0, to_review: 0, waiting: 0 })
    renderWithProviders(<Scoreboard />)
    await waitFor(() => expect(api.categorization.scoreboard).toHaveBeenCalled())
    expect(screen.queryByTestId('categorization-scoreboard')).not.toBeInTheDocument()
  })
})
