import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import NotFoundPage from '@/pages/not-found'
import { renderWithProviders, t } from '@/test/utils'

describe('Not found page', () => {
  it('says the page does not exist and offers a way back to the dashboard', () => {
    renderWithProviders(<NotFoundPage />, { route: '/nao-existe' })

    expect(screen.getByRole('heading', { level: 1, name: t('notFound.title') })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: t('notFound.goHome') })).toHaveAttribute('href', '/')
  })
})
