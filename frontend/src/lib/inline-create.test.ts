import { describe, expect, it } from 'vitest'

import type { Category, CategoryGroup } from '@/types'

import {
  CREATE_NAME_MAX,
  createNameTooLong,
  groupedCreateOptions,
  inlineCreateItemValue,
  inlineCreateName,
} from './inline-create'

describe('inlineCreateName', () => {
  it('offers the trimmed search text when nothing has that name', () => {
    expect(inlineCreateName('  Pet food ', ['Groceries'])).toBe('Pet food')
  })

  it('offers nothing for a blank search', () => {
    expect(inlineCreateName('   ', ['Groceries'])).toBeNull()
  })

  it('treats names that differ only in case or accents as the same', () => {
    expect(inlineCreateName('GROCERIES', ['Groceries'])).toBeNull()
    expect(inlineCreateName('saude', ['Saúde'])).toBeNull()
  })

  it('still offers a name that only partially matches', () => {
    expect(inlineCreateName('Groc', ['Groceries'])).toBe('Groc')
  })
})

describe('inlineCreateItemValue', () => {
  it('contains the search text so the picker filter keeps the row', () => {
    expect(inlineCreateItemValue('Pet food ')).toContain('Pet food ')
  })
})

const category = (id: string, name: string, group_id: string | null) =>
  ({ id, name, group_id, icon: 'x', color: '#000', user_id: 'u', is_system: false, is_hidden: false, treat_as_transfer: false, is_ignored: false }) as Category
const group = (id: string, name: string, categories: Category[] = []) =>
  ({ id, name, icon: 'x', color: '#000', user_id: 'u', position: 0, is_system: false, is_hidden: false, categories }) as CategoryGroup

const mercado = category('c-mercado', 'Mercado', 'g-casa')
const groups = [group('g-transporte', 'Transporte'), group('g-casa', 'Casa', [mercado]), group('g-nimbloo', 'Nimbloo')]
const cats = [mercado]

describe('groupedCreateOptions', () => {
  it('offers the suggested group, another group and a new group, in that order', () => {
    const options = groupedCreateOptions('Carro', cats, groups, 'g-transporte')
    expect(options.map((o) => o.kind)).toEqual(['inGroup', 'pickGroup', 'newGroup'])
    expect(options[0]).toEqual({ kind: 'inGroup', draft: { group: 'Transporte', name: 'Carro', groupExists: true } })
    expect(options[1]).toEqual({ kind: 'pickGroup', name: 'Carro' })
    expect(options[2]).toEqual({ kind: 'newGroup', draft: { group: 'Carro', name: 'Carro', groupExists: false } })
  })

  it('without a suggested group starts with picking one', () => {
    expect(groupedCreateOptions('Carro', cats, groups, null).map((o) => o.kind)).toEqual(['pickGroup', 'newGroup'])
    expect(groupedCreateOptions('Carro', cats, groups, 'gone').map((o) => o.kind)).toEqual(['pickGroup', 'newGroup'])
  })

  it('does not offer another group when the suggested one is the only group', () => {
    const only = [group('g-transporte', 'Transporte')]
    expect(groupedCreateOptions('Carro', [], only, 'g-transporte').map((o) => o.kind)).toEqual(['inGroup', 'newGroup'])
  })

  it('offers nothing for a blank search', () => {
    expect(groupedCreateOptions('   ', cats, groups, 'g-transporte')).toEqual([])
  })

  it('offers nothing when a category already has the name', () => {
    expect(groupedCreateOptions('mercado', cats, groups, 'g-transporte')).toEqual([])
    expect(groupedCreateOptions('MERCADÔ', cats, groups, 'g-transporte')).toEqual([])
  })

  it('does not offer a new group named like an existing group', () => {
    const kinds = groupedCreateOptions('nimblóo', cats, groups, 'g-transporte').map((o) => o.kind)
    expect(kinds).toEqual(['inGroup', 'pickGroup'])
  })

  it('"Group / Name" creates straight in the existing group, under its own spelling', () => {
    expect(groupedCreateOptions('Transporte / Carro', cats, groups, 'g-casa')).toEqual([
      { kind: 'inGroup', draft: { group: 'Transporte', name: 'Carro', groupExists: true } },
    ])
    expect(groupedCreateOptions('transporte/Carro', cats, groups, null)).toEqual([
      { kind: 'inGroup', draft: { group: 'Transporte', name: 'Carro', groupExists: true } },
    ])
  })

  it('"Group / Name" with an unknown group creates both', () => {
    expect(groupedCreateOptions('Jardim / Rosas', cats, groups, 'g-casa')).toEqual([
      { kind: 'inGroup', draft: { group: 'Jardim', name: 'Rosas', groupExists: false } },
    ])
  })

  it('"Group / Name" offers nothing when the group already has that category', () => {
    const carro = category('c-carro', 'Carro', 'g-transporte')
    expect(groupedCreateOptions('transporte / CARRO', [mercado, carro], groups, null)).toEqual([])
  })

  it('"Group / Name" still creates a name that exists in another group', () => {
    expect(groupedCreateOptions('Transporte / Mercado', cats, groups, null)).toEqual([
      { kind: 'inGroup', draft: { group: 'Transporte', name: 'Mercado', groupExists: true } },
    ])
  })

  it('cuts at the first slash only', () => {
    expect(groupedCreateOptions('Casa / Luz / Gás', cats, groups, null)).toEqual([
      { kind: 'inGroup', draft: { group: 'Casa', name: 'Luz / Gás', groupExists: true } },
    ])
  })

  it('offers nothing while one side of the slash is still empty', () => {
    expect(groupedCreateOptions('Casa /', cats, groups, null)).toEqual([])
    expect(groupedCreateOptions('/ Carro', cats, groups, null)).toEqual([])
  })
})

describe('groupedCreateOptions with hidden groups', () => {
  const hidden = { ...group('g-velho', 'Velho'), is_hidden: true }
  const withHidden = [...groups, hidden]

  it('never suggests a hidden group', () => {
    expect(groupedCreateOptions('Carro', cats, withHidden, 'g-velho').map((o) => o.kind)).toEqual([
      'pickGroup',
      'newGroup',
    ])
  })

  it('does not count a hidden group as "another group"', () => {
    const kinds = groupedCreateOptions('Carro', [], [group('g-transporte', 'Transporte'), hidden], 'g-transporte')
    expect(kinds.map((o) => o.kind)).toEqual(['inGroup', 'newGroup'])
  })

  it('offers a new group named like a hidden one (the server makes a visible one)', () => {
    expect(groupedCreateOptions('velho', cats, withHidden, null)).toContainEqual({
      kind: 'newGroup',
      draft: { group: 'velho', name: 'velho', groupExists: false },
    })
  })

  it('"Group / Name" with only a hidden match creates a new group', () => {
    const inHidden = category('c-carro', 'Carro', 'g-velho')
    expect(groupedCreateOptions('velho / Carro', [mercado, inHidden], withHidden, null)).toEqual([
      { kind: 'inGroup', draft: { group: 'velho', name: 'Carro', groupExists: false } },
    ])
  })
})

describe('groupedCreateOptions name cleaning and length', () => {
  it('squeezes inner spaces like the server, in the comparison and in the draft', () => {
    const casaNova = group('g-casa-nova', 'Casa Nova')
    expect(groupedCreateOptions('Casa   Nova /  Luz   e Gás', [], [casaNova], null)).toEqual([
      { kind: 'inGroup', draft: { group: 'Casa Nova', name: 'Luz e Gás', groupExists: true } },
    ])
    expect(groupedCreateOptions('Pet   food', [], groups, 'g-casa')[0]).toEqual({
      kind: 'inGroup',
      draft: { group: 'Casa', name: 'Pet food', groupExists: true },
    })
    expect(groupedCreateOptions('mer  cado', [category('c', 'Mer cado', 'g-casa')], groups, null)).toEqual([])
  })

  it('offers nothing past 100 characters, and says the search is too long', () => {
    const max = 'a'.repeat(CREATE_NAME_MAX)
    const over = 'a'.repeat(CREATE_NAME_MAX + 1)

    expect(groupedCreateOptions(max, cats, groups, 'g-casa').map((o) => o.kind)).toEqual([
      'inGroup',
      'pickGroup',
      'newGroup',
    ])
    expect(createNameTooLong(max)).toBe(false)

    expect(groupedCreateOptions(over, cats, groups, 'g-casa')).toEqual([])
    expect(createNameTooLong(over)).toBe(true)
  })

  it('checks each side of "Group / Name" on its own', () => {
    const over = 'a'.repeat(CREATE_NAME_MAX + 1)
    expect(groupedCreateOptions(`${over} / Carro`, cats, groups, null)).toEqual([])
    expect(groupedCreateOptions(`Casa / ${over}`, cats, groups, null)).toEqual([])
    expect(createNameTooLong(`${over} / Carro`)).toBe(true)
    expect(createNameTooLong(`Casa / ${over}`)).toBe(true)
    // Both sides fit, though the whole search does not.
    const half = 'a'.repeat(60)
    expect(createNameTooLong(`${half} / ${half}`)).toBe(false)
    expect(groupedCreateOptions(`${half} / ${half}`, cats, groups, null)).toHaveLength(1)
  })

  it('counts characters as the server does (an emoji is one)', () => {
    const emojis = '🚗'.repeat(CREATE_NAME_MAX)
    expect(createNameTooLong(emojis)).toBe(false)
    expect(groupedCreateOptions(emojis, cats, groups, null)).not.toEqual([])
  })

  it('squeezed spaces do not count toward the limit', () => {
    const name = `${'a'.repeat(50)}      ${'b'.repeat(49)}`
    expect(createNameTooLong(name)).toBe(false)
  })
})
