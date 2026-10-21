import { describe, expect, it } from 'vitest'

import { readdirSync } from 'node:fs'
import { formatDateInput, formatDateShort, looksCompleteDate, parseDateInput } from './date-input'

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

describe('round-trip in every app locale', () => {
  const locales = readdirSync(new URL('../locales', import.meta.url))
    .filter((f) => f.endsWith('.json') && !f.includes('.test'))
    .map((f) => f.replace('.json', ''))
    .map((l) => (l === 'en' ? 'en-US' : l))

  it('covers the 17 locales', () => expect(locales).toHaveLength(17))

  for (const locale of locales) {
    it(`${locale}: format then parse returns the same ISO date`, () => {
      for (const iso of ['2026-03-25', '2026-02-01', '1999-12-31', '2007-07-09']) {
        expect(parseDateInput(formatDateInput(iso, locale), locale), formatDateInput(iso, locale)).toBe(iso)
      }
    })
  }

  it('parses hu with trailing dot and de/en-US/ja orders', () => {
    expect(formatDateInput('2026-03-25', 'hu')).toMatch(/\.$/)
    expect(parseDateInput('2026. 03. 25.', 'hu')).toBe('2026-03-25')
    expect(parseDateInput('25.03.2026', 'de')).toBe('2026-03-25')
    expect(parseDateInput('3/25/2026', 'en-US')).toBe('2026-03-25')
    expect(parseDateInput('2026-03-25', 'en-US')).toBe('2026-03-25')
    expect(parseDateInput('2026/03/25', 'ja')).toBe('2026-03-25')
    expect(parseDateInput('2026/3/25', 'zh-CN')).toBe('2026-03-25')
  })
})

describe('two-digit years', () => {
  const yy = new Date().getFullYear() % 100
  const pad = (n: number) => String(n).padStart(2, '0')
  it('pivots at current year + 10', () => {
    expect(parseDateInput('1/1/99', 'pt-BR')).toBe('1999-01-01')
    expect(parseDateInput(`1/1/${pad((yy + 10) % 100)}`, 'pt-BR')).toBe(`${2000 + yy + 10}-01-01`)
    expect(parseDateInput(`1/1/${pad((yy + 11) % 100)}`, 'pt-BR')).toBe(`${1900 + yy + 11}-01-01`)
  })
})

describe('looksCompleteDate', () => {
  it('is false mid-typing and true once the year looks finished', () => {
    expect(looksCompleteDate('01/02/2')).toBe(false)
    expect(looksCompleteDate('01/02')).toBe(false)
    expect(looksCompleteDate('01/02/26')).toBe(true)
    expect(looksCompleteDate('31/02/2026')).toBe(true)
  })
})
