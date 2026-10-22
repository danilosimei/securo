import { describe, it, expect } from 'vitest'
import { navItems, visibleNavItems, type NavItem } from './nav-items'
import { MODULE_IDS, type ModuleId } from './modules'

const all = () => true
const none = () => false
const allExcept =
  (...off: ModuleId[]) =>
  (id: ModuleId) =>
    !off.includes(id)

// Mirrors the backend's PersonalPolicy result. Spelled out rather than
// derived, so a change that takes a link away from existing users has
// to be a deliberate edit here.
const PERSONAL_MODULES: ModuleId[] = [
  'transactions',
  'accounts',
  'import',
  'reports',
  'assets',
  'budgets',
  'goals',
  'recurring',
  'categories',
  'payees',
  'split_groups',
  'rules',
]

const linkKeys = (items: NavItem[]) =>
  items.filter((i) => i.type === 'link').map((i) => i.key)

describe('nav catalog', () => {
  it('maps every link to a known module', () => {
    for (const item of navItems) {
      if (item.type !== 'link' || !item.module) continue
      expect(MODULE_IDS).toContain(item.module)
    }
  })

  it('has a link for every module in the catalog', () => {
    const covered = navItems.flatMap((i) => (i.type === 'link' && i.module ? [i.module] : []))
    expect([...new Set(covered)].sort()).toEqual([...MODULE_IDS].sort())
  })

  it('gives every link a distinct module, except the ones that live inside another', () => {
    // Categorization is a view over the categories module, and collections
    // live with accounts: neither is a module of its own.
    const sharesModule = new Set(['categorization', 'collections', 'dashboard'])
    const covered = navItems
      .filter((i) => i.type === 'link' && !sharesModule.has(i.key))
      .map((i) => (i.type === 'link' ? i.module : ''))
    expect(new Set(covered).size).toBe(covered.length)
  })
})

describe('visibleNavItems', () => {
  it('renders a personal workspace exactly as before modules existed', () => {
    const personal = visibleNavItems(navItems, (id) => PERSONAL_MODULES.includes(id))
    expect(linkKeys(personal)).toEqual([
      'dashboard',
      'transactions',
      'accounts',
      'import',
      'budgets',
      'goals',
      'recurring',
      'reports',
      'assets',
      'categories',
      'categorization',
      'rules',
      'payees',
      'splitGroups',
      'collections',
    ])
    // All four section headers survive.
    expect(personal.filter((i) => i.type === 'separator')).toHaveLength(4)
  })

  it('adds invoices for a workspace that has it', () => {
    const business = visibleNavItems(navItems, all)
    expect(linkKeys(business)).toContain('invoices')
    // And it does not disturb the rest of the order.
    expect(linkKeys(business).indexOf('invoices')).toBe(3)
  })

  it('hides a section header once its last link goes', () => {
    const items: NavItem[] = [
      { type: 'separator', labelKey: 'a' },
      { type: 'link', key: 'one', path: '/1', icon: () => null, module: 'reports' },
      { type: 'separator', labelKey: 'b' },
      { type: 'link', key: 'two', path: '/2', icon: () => null, module: 'assets' },
    ]
    const result = visibleNavItems(items, allExcept('reports'))
    expect(result.map((i) => (i.type === 'separator' ? i.labelKey : i.key))).toEqual([
      'b',
      'two',
    ])
  })

  it('keeps a section header while any of its links remain', () => {
    const result = visibleNavItems(navItems, allExcept('transactions', 'invoices'))
    const labels = result.filter((i) => i.type === 'separator').map((i) => i.labelKey)
    expect(labels).toContain('nav.groupDaily')
    expect(linkKeys(result)).toContain('accounts')
  })

  it('drops every header when no module is on, keeping only the dashboard', () => {
    expect(linkKeys(visibleNavItems(navItems, none))).toEqual(['dashboard'])
    expect(visibleNavItems(navItems, none).some((i) => i.type === 'separator')).toBe(false)
  })

  it('leaves a trailing header out rather than dangling', () => {
    const items: NavItem[] = [
      { type: 'link', key: 'one', path: '/1', icon: () => null, module: 'reports' },
      { type: 'separator', labelKey: 'trailing' },
      { type: 'link', key: 'two', path: '/2', icon: () => null, module: 'assets' },
    ]
    const result = visibleNavItems(items, allExcept('assets'))
    expect(result.map((i) => (i.type === 'separator' ? i.labelKey : i.key))).toEqual(['one'])
  })
})
