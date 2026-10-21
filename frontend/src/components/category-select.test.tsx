import { useState, type ReactNode } from 'react'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithProviders, t } from '@/test/utils'
import { WorkspaceContext } from '@/contexts/workspace-context'
import type { Category, CategoryGroup } from '@/types'

const { create } = vi.hoisted(() => ({ create: vi.fn() }))
vi.mock('@/lib/api', () => ({ categories: { create } }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { CategorySelect } from './category-select'

const groceries = {
  id: 'cat-1',
  name: 'Groceries',
  icon: 'shopping-cart',
  color: '#10b981',
  group_id: null,
} as unknown as Category

function withRole(canWrite: boolean) {
  return function Workspace({ children }: { children: ReactNode }) {
    return (
      <WorkspaceContext.Provider
        value={{ canWrite } as unknown as React.ContextType<typeof WorkspaceContext>}
      >
        {children}
      </WorkspaceContext.Provider>
    )
  }
}

function renderSelect({ canWrite = true, creatable = true } = {}) {
  const onChange = vi.fn()
  const Workspace = withRole(canWrite)
  // Holds the value like a real form does, so a test sees what the trigger
  // shows after a change rather than only that a callback ran.
  function Harness() {
    const [value, setValue] = useState('')
    return (
      <CategorySelect
        value={value}
        onChange={(v) => { setValue(v); onChange(v) }}
        categories={[groceries]}
        groups={[]}
        allowNone
        creatable={creatable}
      />
    )
  }
  const utils = renderWithProviders(
    <Workspace>
      <Harness />
    </Workspace>,
  )
  return { ...utils, onChange }
}

// The picker's one search box, whatever its placeholder says.
const searchBox = () => screen.getByRole('combobox')

async function openAndType(user: ReturnType<typeof renderWithProviders>['user'], text: string) {
  await user.click(screen.getByRole('button'))
  await user.type(searchBox(), text)
}

describe('CategorySelect inline creation', () => {
  beforeEach(() => {
    create.mockReset()
  })

  it('creates the searched category and selects it', async () => {
    create.mockResolvedValue({ ...groceries, id: 'cat-new', name: 'Pet food', color: '#6366f1' })
    const { user, onChange, queryClient } = renderSelect()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')

    await openAndType(user, 'Pet food')
    await user.click(screen.getByText(t('common.createNamed', { name: 'Pet food' })))

    await waitFor(() => expect(onChange).toHaveBeenCalledWith('cat-new'))
    // The supplied list has not refetched, yet the trigger shows the new category.
    expect(screen.getByRole('button', { name: 'Pet food' })).toBeInTheDocument()
    expect(create).toHaveBeenCalledWith({ name: 'Pet food', icon: 'circle-help', color: '#6366f1' })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['categories'] })
  })

  it('does not offer to create a name that already exists', async () => {
    const { user } = renderSelect()

    await openAndType(user, 'groceries')

    expect(screen.queryByText(t('common.createNamed', { name: 'groceries' }))).not.toBeInTheDocument()
  })

  it('never offers creation to a viewer', async () => {
    const { user } = renderSelect({ canWrite: false })

    await openAndType(user, 'Pet food')

    expect(screen.queryByText(t('common.createNamed', { name: 'Pet food' }))).not.toBeInTheDocument()
  })

  it('does not offer creation unless the picker opts in', async () => {
    const { user } = renderSelect({ creatable: false })

    await openAndType(user, 'Pet food')

    expect(screen.queryByText(t('common.createNamed', { name: 'Pet food' }))).not.toBeInTheDocument()
  })
})

describe('CategorySelect creation in a group', () => {
  const mercado = { ...groceries, id: 'cat-mercado', name: 'Mercado', group_id: 'g-casa' } as Category
  const groupsFixture = [
    { id: 'g-transporte', name: 'Transporte', categories: [] },
    { id: 'g-casa', name: 'Casa', categories: [mercado] },
  ] as unknown as CategoryGroup[]

  function renderGrouped({
    canWrite = true,
    footerAction,
  }: { canWrite?: boolean; footerAction?: React.ComponentProps<typeof CategorySelect>['footerAction'] } = {}) {
    const onCreateInGroup = vi.fn()
    const onChange = vi.fn()
    const Workspace = withRole(canWrite)
    const utils = renderWithProviders(
      <Workspace>
        <CategorySelect
          value=""
          onChange={onChange}
          categories={[mercado]}
          groups={groupsFixture}
          placeholder="Other…"
          creatable
          onCreateInGroup={onCreateInGroup}
          suggestedGroupId="g-transporte"
          footerAction={footerAction}
        />
      </Workspace>,
    )
    return { ...utils, onCreateInGroup, onChange }
  }

  const inGroup = (name: string, group: string) => t('categorization.create.inGroup', { name, group })
  const pickGroup = (name: string) => t('categorization.create.pickGroup', { name })

  beforeEach(() => {
    create.mockReset()
  })

  it('creates the searched name in the suggested group', async () => {
    const { user, onCreateInGroup } = renderGrouped()

    await openAndType(user, 'Carro')
    expect(screen.getByText(pickGroup('Carro'))).toBeInTheDocument()
    expect(screen.getByText(t('categorization.create.newGroup', { name: 'Carro' }))).toBeInTheDocument()
    // The flat "Create" of `creatable` gives way to the grouped options.
    expect(screen.queryByText(t('common.createNamed', { name: 'Carro' }))).not.toBeInTheDocument()
    await user.click(screen.getByText(inGroup('Carro', 'Transporte')))

    expect(onCreateInGroup).toHaveBeenCalledWith({ group: 'Transporte', name: 'Carro', groupExists: true })
    expect(create).not.toHaveBeenCalled()
    // The popover closed.
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('Enter takes the first option when nothing else matches', async () => {
    const { user, onCreateInGroup } = renderGrouped()

    await openAndType(user, 'Carro{Enter}')

    expect(onCreateInGroup).toHaveBeenCalledWith({ group: 'Transporte', name: 'Carro', groupExists: true })
  })

  it('"in another group" lists the groups, searchable, and Enter picks one', async () => {
    const { user, onCreateInGroup } = renderGrouped()

    await openAndType(user, 'Carro')
    await user.click(screen.getByText(pickGroup('Carro')))

    const search = screen.getByPlaceholderText(t('categorization.create.chooseGroup'))
    await waitFor(() => expect(search).toHaveFocus())
    expect(screen.getByRole('option', { name: 'Casa' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /Mercado/ })).not.toBeInTheDocument()
    await user.type(search, 'cas{Enter}')

    expect(onCreateInGroup).toHaveBeenCalledWith({ group: 'Casa', name: 'Carro', groupExists: true })
  })

  it('clicking a group in the second step creates there', async () => {
    const { user, onCreateInGroup } = renderGrouped()

    await openAndType(user, 'Carro')
    await user.click(screen.getByText(pickGroup('Carro')))
    await user.click(screen.getByRole('option', { name: 'Casa' }))

    expect(onCreateInGroup).toHaveBeenCalledWith({ group: 'Casa', name: 'Carro', groupExists: true })
  })

  it('Escape in the second step goes back to the first, search kept', async () => {
    const { user, onCreateInGroup } = renderGrouped()

    await openAndType(user, 'Carro')
    await user.click(screen.getByText(pickGroup('Carro')))
    await user.keyboard('{Escape}')

    const search = searchBox()
    expect(search).toHaveValue('Carro')
    await waitFor(() => expect(search).toHaveFocus())
    expect(screen.getByText(inGroup('Carro', 'Transporte'))).toBeInTheDocument()
    expect(onCreateInGroup).not.toHaveBeenCalled()

    // A second Escape closes the picker as usual.
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('Backspace on an empty group search goes back too', async () => {
    const { user } = renderGrouped()

    await openAndType(user, 'Carro')
    await user.click(screen.getByText(pickGroup('Carro')))
    await user.type(screen.getByPlaceholderText(t('categorization.create.chooseGroup')), 'c{Backspace}{Backspace}')

    expect(searchBox()).toHaveValue('Carro')
  })

  it('"Group / Name" offers one option, creating the group when it is new', async () => {
    const { user, onCreateInGroup } = renderGrouped()

    await openAndType(user, 'Jardim / Rosas')
    expect(screen.queryByText(pickGroup('Rosas'))).not.toBeInTheDocument()
    await user.click(screen.getByText(t('categorization.create.inNewGroup', { name: 'Rosas', group: 'Jardim' })))

    expect(onCreateInGroup).toHaveBeenCalledWith({ group: 'Jardim', name: 'Rosas', groupExists: false })
  })

  it('a name that already exists offers no creation', async () => {
    const { user } = renderGrouped()

    await openAndType(user, 'mercado')

    expect(screen.queryByText(inGroup('mercado', 'Transporte'))).not.toBeInTheDocument()
    expect(screen.getByRole('option', { name: /Mercado/ })).toBeInTheDocument()
  })

  it('a viewer sees no create option', async () => {
    const { user } = renderGrouped({ canWrite: false })

    await openAndType(user, 'Carro')

    expect(screen.queryByText(inGroup('Carro', 'Transporte'))).not.toBeInTheDocument()
    expect(screen.queryByText(pickGroup('Carro'))).not.toBeInTheDocument()
    expect(screen.queryByText(t('categorization.create.newGroup', { name: 'Carro' }))).not.toBeInTheDocument()
  })

  it('the footer action is always the last item and runs on select', async () => {
    const onSelect = vi.fn()
    const { user } = renderGrouped({ footerAction: { label: 'Ask the AI', onSelect } })

    await openAndType(user, 'Carro')
    const options = screen.getAllByRole('option')
    expect(options[options.length - 1]).toHaveTextContent('Ask the AI')
    await user.clear(searchBox())
    await user.type(searchBox(), 'zzz')
    await user.click(screen.getByRole('option', { name: 'Ask the AI' }))

    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('a disabled footer action explains why and does nothing', async () => {
    const onSelect = vi.fn()
    const { user } = renderGrouped({
      canWrite: false,
      footerAction: { label: 'Ask the AI', disabled: true, hint: 'Read-only access', onSelect },
    })

    await user.click(screen.getByRole('button'))
    const action = screen.getByRole('option', { name: 'Ask the AI' })
    expect(action).toHaveAttribute('aria-disabled', 'true')
    expect(action).toHaveAttribute('title', 'Read-only access')
    await user.click(action)

    expect(onSelect).not.toHaveBeenCalled()
  })
})

describe('CategorySelect creation in a group: limits and hidden groups', () => {
  const groupsFixture = [
    { id: 'g-transporte', name: 'Transporte', is_hidden: false, categories: [] },
    { id: 'g-casa', name: 'Casa', is_hidden: false, categories: [] },
    { id: 'g-velho', name: 'Velho', is_hidden: true, categories: [] },
  ] as unknown as CategoryGroup[]

  function renderGrouped() {
    const onCreateInGroup = vi.fn()
    const Workspace = withRole(true)
    const utils = renderWithProviders(
      <Workspace>
        <CategorySelect
          value=""
          onChange={vi.fn()}
          categories={[]}
          groups={groupsFixture}
          placeholder="Other…"
          onCreateInGroup={onCreateInGroup}
          suggestedGroupId="g-transporte"
        />
      </Workspace>,
    )
    return { ...utils, onCreateInGroup }
  }

  const tooLong = () => t('categorization.create.tooLong', { max: 100 })
  const pickGroup = (name: string) => t('categorization.create.pickGroup', { name })

  async function openGroupStep(user: ReturnType<typeof renderWithProviders>['user'], name: string) {
    await openAndType(user, name)
    await user.click(screen.getByText(pickGroup(name)))
    return screen.getByPlaceholderText(t('categorization.create.chooseGroup'))
  }

  it('a name over 100 characters offers no creation and says why, disabled', async () => {
    const { user, onCreateInGroup } = renderGrouped()
    const over = 'a'.repeat(101)

    await user.click(screen.getByRole('button'))
    // Pasted rather than typed: 101 key presses add nothing to the test.
    searchBox().focus()
    await user.paste(over)

    const reason = screen.getByRole('option', { name: tooLong() })
    expect(reason).toHaveAttribute('aria-disabled', 'true')
    expect(reason).toHaveAttribute('title', tooLong())
    expect(screen.queryByText(pickGroup(over))).not.toBeInTheDocument()
    await user.click(reason)
    await user.keyboard('{Enter}')
    expect(onCreateInGroup).not.toHaveBeenCalled()
  })

  it('exactly 100 characters is still offered', async () => {
    const { user } = renderGrouped()
    const max = 'a'.repeat(100)

    await user.click(screen.getByRole('button'))
    searchBox().focus()
    await user.paste(max)

    expect(screen.getByText(pickGroup(max))).toBeInTheDocument()
    expect(screen.queryByText(tooLong())).not.toBeInTheDocument()
  })

  it('the group step lists visible groups only, the suggested one first, each with its title', async () => {
    const { user } = renderGrouped()

    await openGroupStep(user, 'Carro')

    const names = screen.getAllByRole('option').map((o) => o.textContent)
    expect(names).toEqual(['Transporte', 'Casa'])
    expect(screen.getByRole('option', { name: 'Casa' })).toHaveAttribute('title', 'Casa')
  })

  it('typing the suggested group in the group step still finds it', async () => {
    const { user, onCreateInGroup } = renderGrouped()

    const search = await openGroupStep(user, 'Carro')
    await user.type(search, 'transp{Enter}')

    expect(onCreateInGroup).toHaveBeenCalledWith({ group: 'Transporte', name: 'Carro', groupExists: true })
  })

  it('typing a hidden group name in the group step creates a new visible group', async () => {
    const { user, onCreateInGroup } = renderGrouped()

    const search = await openGroupStep(user, 'Carro')
    await user.type(search, 'Velho')
    const label = t('categorization.create.inNewGroup', { name: 'Carro', group: 'Velho' })
    expect(screen.getByRole('option', { name: label })).toHaveAttribute('title', label)
    expect(screen.queryByRole('option', { name: 'Velho' })).not.toBeInTheDocument()
    await user.keyboard('{Enter}')

    expect(onCreateInGroup).toHaveBeenCalledWith({ group: 'Velho', name: 'Carro', groupExists: false })
  })

  it('a group name over 100 characters in the group step is refused with the reason', async () => {
    const { user, onCreateInGroup } = renderGrouped()

    const search = await openGroupStep(user, 'Carro')
    search.focus()
    await user.paste('g'.repeat(101))

    expect(screen.getByRole('option', { name: tooLong() })).toHaveAttribute('aria-disabled', 'true')
    await user.keyboard('{Enter}')
    expect(onCreateInGroup).not.toHaveBeenCalled()
  })

  it('"Hidden / Name" creates a new group rather than the hidden one', async () => {
    const { user, onCreateInGroup } = renderGrouped()

    await openAndType(user, 'velho / Carro')
    await user.click(screen.getByText(t('categorization.create.inNewGroup', { name: 'Carro', group: 'velho' })))

    expect(onCreateInGroup).toHaveBeenCalledWith({ group: 'velho', name: 'Carro', groupExists: false })
  })
})

describe('CategorySelect: creation is visible before typing', () => {
  const mercado = { ...groceries, id: 'cat-mercado', name: 'Mercado', group_id: 'g-casa' } as Category
  const groupsFixture = [
    { id: 'g-transporte', name: 'Transporte', categories: [] },
    { id: 'g-casa', name: 'Casa', categories: [mercado] },
  ] as unknown as CategoryGroup[]
  const start = () => t('categorization.create.start')

  function renderGrouped({ canWrite = true } = {}) {
    const onCreateInGroup = vi.fn()
    const onChange = vi.fn()
    const Workspace = withRole(canWrite)
    const utils = renderWithProviders(
      <Workspace>
        <CategorySelect
          value=""
          onChange={onChange}
          categories={[mercado]}
          groups={groupsFixture}
          placeholder="Other…"
          onCreateInGroup={onCreateInGroup}
          suggestedGroupId="g-transporte"
          footerAction={{ label: 'Ask the AI', onSelect: vi.fn() }}
        />
      </Workspace>,
    )
    return { ...utils, onCreateInGroup, onChange }
  }

  it('opens with "New category or group" first and a search that says it creates', async () => {
    const { user } = renderGrouped()

    await user.click(screen.getByRole('button'))

    expect(searchBox()).toHaveAttribute('placeholder', t('categorization.create.searchOrCreate'))
    expect(screen.getAllByRole('option')[0]).toHaveTextContent(start())
  })

  it('the start row leaves while searching, so Enter still picks a match', async () => {
    const { user, onChange, onCreateInGroup } = renderGrouped()

    await openAndType(user, 'merc')
    expect(screen.queryByRole('option', { name: start() })).not.toBeInTheDocument()
    await user.keyboard('{Enter}')

    expect(onChange).toHaveBeenCalledWith('cat-mercado')
    expect(onCreateInGroup).not.toHaveBeenCalled()
  })

  it('the start row opens a naming step that lists only the ways to create', async () => {
    const { user, onCreateInGroup } = renderGrouped()

    await user.click(screen.getByRole('button'))
    await user.click(screen.getByRole('option', { name: start() }))

    const name = searchBox()
    expect(name).toHaveAttribute('placeholder', t('categorization.create.namePlaceholder'))
    await waitFor(() => expect(name).toHaveFocus())
    expect(screen.getByText(t('categorization.create.typeName'))).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /Mercado/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Ask the AI' })).not.toBeInTheDocument()

    await user.type(name, 'Jardinagem')
    expect(screen.getByText(t('categorization.create.pickGroup', { name: 'Jardinagem' }))).toBeInTheDocument()
    expect(screen.getByText(t('categorization.create.newGroup', { name: 'Jardinagem' }))).toBeInTheDocument()
    await user.keyboard('{Enter}')

    expect(onCreateInGroup).toHaveBeenCalledWith({ group: 'Transporte', name: 'Jardinagem', groupExists: true })
  })

  it('a name that already exists says so in the naming step', async () => {
    const { user } = renderGrouped()

    await user.click(screen.getByRole('button'))
    await user.click(screen.getByRole('option', { name: start() }))
    await user.type(searchBox(), 'mercado')

    expect(screen.getByText(t('categorization.create.exists'))).toBeInTheDocument()
    expect(screen.queryAllByRole('option')).toHaveLength(0)
  })

  it('Escape in the naming step goes back to the list, then closes', async () => {
    const { user } = renderGrouped()

    await user.click(screen.getByRole('button'))
    await user.click(screen.getByRole('option', { name: start() }))
    await user.keyboard('{Escape}')

    expect(searchBox()).toHaveAttribute('placeholder', t('categorization.create.searchOrCreate'))
    expect(screen.getByRole('option', { name: /Mercado/ })).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('the group step goes back to the naming step with the name kept', async () => {
    const { user } = renderGrouped()

    await user.click(screen.getByRole('button'))
    await user.click(screen.getByRole('option', { name: start() }))
    await user.type(searchBox(), 'Jardinagem')
    await user.click(screen.getByText(t('categorization.create.pickGroup', { name: 'Jardinagem' })))
    await user.keyboard('{Escape}')

    expect(searchBox()).toHaveAttribute('placeholder', t('categorization.create.namePlaceholder'))
    expect(searchBox()).toHaveValue('Jardinagem')
  })

  it('a viewer gets neither the start row nor the create wording', async () => {
    const { user } = renderGrouped({ canWrite: false })

    await user.click(screen.getByRole('button'))

    expect(screen.queryByRole('option', { name: start() })).not.toBeInTheDocument()
    expect(searchBox()).toHaveAttribute('placeholder', t('transactions.searchCategory'))
  })

  it('a flat creatable picker gets the start row too and creates the typed name', async () => {
    create.mockReset()
    create.mockResolvedValue({ ...groceries, id: 'cat-new', name: 'Pet food', color: '#6366f1' })
    const { user, onChange } = renderSelect()

    await user.click(screen.getByRole('button'))
    await user.click(screen.getByRole('option', { name: `${t('categorization.create.naming')}…` }))
    expect(searchBox()).toHaveAttribute('placeholder', t('categorization.create.nameOnly'))
    expect(screen.getByText(t('categorization.create.typeNameOnly'))).toBeInTheDocument()
    await user.type(searchBox(), 'Pet food{Enter}')

    await waitFor(() => expect(onChange).toHaveBeenCalledWith('cat-new'))
  })
})
