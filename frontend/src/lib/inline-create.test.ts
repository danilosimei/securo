import { describe, expect, it } from 'vitest'

import type { Category, CategoryGroup } from '@/types'

import { groupedCreateOptions, inlineCreateItemValue, inlineCreateName } from './inline-create'

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
