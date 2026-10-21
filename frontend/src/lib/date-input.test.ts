import { describe, expect, it } from 'vitest'

import { formatDateInput, formatDateShort, parseDateInput } from './date-input'

describe('parseDateInput', () => {
  it('accepts d/m/yy, dd/mm/yyyy and ISO in a day-first locale', () => {
    expect(parseDateInput('1/2/26', 'pt-BR')).toBe('2026-02-01')
    expect(parseDateInput('01/02/2026', 'pt-BR')).toBe('2026-02-01')
    expect(parseDateInput('2026-02-01', 'pt-BR')).toBe('2026-02-01')
  })

  it('follows the locale field order', () => {
    expect(parseDateInput('2/1/2026', 'en-US')).toBe('2026-02-01')
    expect(parseDateInput('2/1/2026', 'pt-BR')).toBe('2026-01-02')
  })

  it('tolerates dots, dashes and spaces', () => {
    expect(parseDateInput(' 1.2.2026 ', 'pt-BR')).toBe('2026-02-01')
    expect(parseDateInput('1-2-26', 'pt-BR')).toBe('2026-02-01')
  })

  it('rejects impossible or incomplete dates', () => {
    expect(parseDateInput('31/02/2026', 'pt-BR')).toBeNull()
    expect(parseDateInput('1/2', 'pt-BR')).toBeNull()
    expect(parseDateInput('1/2/202', 'pt-BR')).toBeNull()
    expect(parseDateInput('abc', 'pt-BR')).toBeNull()
    expect(parseDateInput('', 'pt-BR')).toBeNull()
  })
})

describe('formatDateInput / formatDateShort', () => {
  it('formats the field text with a four-digit year', () => {
    expect(formatDateInput('2026-02-01', 'pt-BR')).toBe('01/02/2026')
    expect(formatDateInput('', 'pt-BR')).toBe('')
  })

  it('formats the compact label with a two-digit year', () => {
    expect(formatDateShort('2026-02-01', 'pt-BR')).toBe('01/02/26')
  })
})
