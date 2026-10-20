import { describe, expect, it } from 'vitest'

import { predictTree } from '@/lib/categorization-organizer'
import type { Category, CategoryGroup, OrganizerOp } from '@/types'

const cat = (id: string, name: string, group_id: string | null): Category => ({
  id,
  name,
  group_id,
  icon: 'circle-help',
  color: '#10b981',
  user_id: 'u',
  is_system: false,
  is_hidden: false,
  treat_as_transfer: false,
  is_ignored: false,
})
const grp = (id: string, name: string, categories: Category[]): CategoryGroup => ({
  id,
  name,
  icon: 'folder',
  color: '#6366f1',
  user_id: 'u',
  position: 0,
  is_system: false,
  is_hidden: false,
  categories,
})

function op(overrides: Partial<OrganizerOp> & Pick<OrganizerOp, 'id' | 'op'>): OrganizerOp {
  return {
    target: null,
    group: null,
    name: null,
    children: [],
    into: null,
    why: '',
    impact: { tx: 0, total: 0 },
    labels: {},
    status: 'pending',
    decided_at: null,
    result: null,
    ...overrides,
  }
}

const lazer = cat('lazer', 'Lazer', 'outros')
const pets = cat('pets', 'Pets', 'outros')
const compras = cat('compras', 'Compras', 'casa')
const online = cat('online', 'Compras Online', 'casa')
const mercado = cat('mercado', 'Mercado', 'casa')
const solta = cat('solta', 'Avulsa', null)
const groups = [grp('outros', 'Outros', [lazer, pets]), grp('casa', 'Casa', [compras, online, mercado])]
const categories = [lazer, pets, compras, online, mercado, solta]

const names = (tree: ReturnType<typeof predictTree>) =>
  tree.map((g) => [g.name, g.categories.map((c) => c.name)])

describe('predictTree', () => {
  it('without ops is the current tree, ungrouped last, nothing touched', () => {
    const tree = predictTree(groups, categories, [])
    expect(names(tree)).toEqual([
      ['Outros', ['Lazer', 'Pets']],
      ['Casa', ['Compras', 'Compras Online', 'Mercado']],
      [null, ['Avulsa']],
    ])
    expect(tree.every((g) => !g.touched)).toBe(true)
  })

  it('splits a category into a new group: first child keeps it, the rest are new', () => {
    const tree = predictTree(groups, categories, [
      op({
        id: 'o1',
        op: 'split_category',
        target: 'c1',
        name: 'Diversão',
        children: ['Assinaturas', 'Ingressos', 'Cinema'],
        labels: { target: 'Lazer', children: ['Assinaturas', 'Ingressos', 'Cinema'] },
      }),
    ])
    const diversao = tree.find((g) => g.name === 'Diversão')!
    expect(diversao.isNew).toBe(true)
    expect(diversao.categories.map((c) => [c.name, c.isNew, c.renamedFrom, c.movedFrom])).toEqual([
      ['Assinaturas', false, 'Lazer', 'Outros'],
      ['Ingressos', true, undefined, undefined],
      ['Cinema', true, undefined, undefined],
    ])
    expect(tree.find((g) => g.name === 'Outros')!.categories.map((c) => c.name)).toEqual(['Pets'])
  })

  it('a split into a group named like the category reuses that group', () => {
    const tree = predictTree([...groups, grp('lz', 'Lazer', [])], categories, [
      op({ id: 'o1', op: 'split_category', target: 'c1', name: 'Lazer', children: ['Jogos', 'Shows'], labels: { target: 'Lazer' } }),
    ])
    expect(tree.filter((g) => g.name === 'Lazer')).toHaveLength(1)
    expect(tree.find((g) => g.name === 'Lazer')!.categories.map((c) => c.name)).toEqual(['Jogos', 'Shows'])
  })

  it('merge keeps the source in place, marked with where it goes', () => {
    const tree = predictTree(groups, categories, [
      op({ id: 'o1', op: 'merge', target: 'c4', into: 'c3', labels: { target: 'Compras Online', into: 'Compras' } }),
    ])
    const casa = tree.find((g) => g.name === 'Casa')!
    expect(casa.categories.find((c) => c.name === 'Compras Online')!.mergedInto).toBe('Compras')
    expect(casa.categories.find((c) => c.name === 'Compras')!.opIds).toEqual(['o1'])
  })

  it('create_group + create_category + move resolve new groups by name', () => {
    const tree = predictTree(groups, categories, [
      op({ id: 'o1', op: 'create_group', name: 'Bichos' }),
      op({ id: 'o2', op: 'move_category', target: 'c2', group: 'Bichos', labels: { target: 'Pets', group: 'Bichos' } }),
      op({ id: 'o3', op: 'create_category', group: 'Bichos', name: 'Veterinário', children: ['i1'], labels: { group: 'Bichos', children: ['Pet Vet'] } }),
      op({ id: 'o4', op: 'create_category', group: 'g2', name: 'Farmácia', labels: { group: 'Casa' } }),
    ])
    const bichos = tree.find((g) => g.name === 'Bichos')!
    expect(bichos.isNew).toBe(true)
    expect(bichos.categories.map((c) => [c.name, c.isNew, c.movedFrom])).toEqual([
      ['Pets', false, 'Outros'],
      ['Veterinário', true, undefined],
    ])
    expect(tree.find((g) => g.name === 'Casa')!.categories.at(-1)).toMatchObject({ name: 'Farmácia', isNew: true })
  })

  it('renames a group or a category, keeping the old name as a hint', () => {
    const tree = predictTree(groups, categories, [
      op({ id: 'o1', op: 'rename', target: 'g2', name: 'Lar', labels: { target: 'Casa' } }),
      op({ id: 'o2', op: 'rename', target: 'c5', name: 'Supermercado', labels: { target: 'Mercado' } }),
    ])
    const lar = tree.find((g) => g.name === 'Lar')!
    expect(lar.renamedFrom).toBe('Casa')
    expect(lar.categories.find((c) => c.name === 'Supermercado')!.renamedFrom).toBe('Mercado')
  })

  it('skips decided ops and finds a category by the name an applied op gave it', () => {
    // o1 already renamed Lazer to Diversão (the tree now says so); o2 still
    // names it "Lazer" in its labels.
    const renamed = cat('lazer', 'Diversão', 'outros')
    const tree = predictTree(
      [grp('outros', 'Outros', [renamed, pets]), groups[1]],
      [renamed, pets, compras, online, mercado],
      [
        op({ id: 'o1', op: 'rename', target: 'c1', name: 'Diversão', status: 'applied', labels: { target: 'Lazer' } }),
        op({ id: 'o2', op: 'move_category', target: 'c1', group: 'g2', labels: { target: 'Lazer', group: 'Casa' } }),
        op({ id: 'o3', op: 'merge', target: 'c2', into: 'c3', status: 'rejected', labels: { target: 'Pets', into: 'Compras' } }),
      ],
    )
    expect(tree.find((g) => g.name === 'Casa')!.categories.at(-1)).toMatchObject({
      name: 'Diversão',
      movedFrom: 'Outros',
    })
    expect(tree.find((g) => g.name === 'Outros')!.categories[0]).toMatchObject({ name: 'Pets', opIds: [] })
  })

  it('ignores ops naming categories it cannot find', () => {
    const tree = predictTree(groups, categories, [
      op({ id: 'o1', op: 'rename', target: 'c9', name: 'X', labels: { target: 'Sumida' } }),
    ])
    expect(names(tree)).toEqual(names(predictTree(groups, categories, [])))
  })
})
