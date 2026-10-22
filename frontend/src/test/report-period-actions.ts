/** Driving the Reports period menu from a test. */
import { expect } from 'vitest'
import { screen } from '@testing-library/react'
import type userEvent from '@testing-library/user-event'
import { t } from '@/test/utils'

type User = ReturnType<typeof userEvent.setup>

/** Opens the period menu and picks the preset with this label. */
export async function pickPeriod(user: User, label: string) {
  await user.click(screen.getByTestId('period-menu'))
  await user.click(await screen.findByRole('menuitemradio', { name: label }))
}

/** The period menu shows this preset as the chosen one. */
export function expectPeriod(label: string) {
  expect(screen.getByTestId('period-menu')).toHaveAccessibleName(t('reports.periodMenu', { period: label }))
}
