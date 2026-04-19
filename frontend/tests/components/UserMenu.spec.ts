import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { elementPlusModuleMock } from '../helpers/elementPlusStubs'

vi.mock('element-plus', () => elementPlusModuleMock())

// We need to control the user ref across tests. Define it at module scope
// so vi.mock factory can close over it.
const testUser = ref<{
  id: string
  username: string
  avatar_url: string | null
  role: string
} | null>(null)

const mockLogout = vi.fn()

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({
    user: testUser,
    logout: mockLogout,
  }),
}))

import { mount } from '@vue/test-utils'
import UserMenu from '@/components/UserMenu.vue'

beforeEach(() => {
  testUser.value = null
  vi.clearAllMocks()
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('UserMenu', () => {
  it('renders nothing visible when user is null', () => {
    const wrapper = mount(UserMenu)
    expect(wrapper.text()).toBe('')
  })

  it('renders the username when user is set', () => {
    testUser.value = { id: '1', username: 'alice', avatar_url: null, role: 'downloader' }
    const wrapper = mount(UserMenu)
    expect(wrapper.text()).toContain('alice')
  })

  it('renders avatar img when avatar_url is present', () => {
    testUser.value = {
      id: '2',
      username: 'bob',
      avatar_url: 'https://cdn.discordapp.com/avatars/2/hash.png',
      role: 'admin',
    }
    const wrapper = mount(UserMenu)
    const img = wrapper.find('img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe('https://cdn.discordapp.com/avatars/2/hash.png')
  })

  it('renders placeholder initial letter when no avatar_url', () => {
    testUser.value = { id: '3', username: 'carol', avatar_url: null, role: 'downloader' }
    const wrapper = mount(UserMenu)
    expect(wrapper.text()).toContain('C')
  })

  it('shows 登出 in the dropdown', () => {
    testUser.value = { id: '4', username: 'dave', avatar_url: null, role: 'admin' }
    const wrapper = mount(UserMenu, {
      global: {
        stubs: {
          ElDropdown: {
            template: '<div class="el-dropdown"><slot /><slot name="dropdown" /></div>',
          },
          ElDropdownMenu: { template: '<ul><slot /></ul>' },
          ElDropdownItem: {
            props: ['disabled', 'divided'],
            template: '<li><slot /></li>',
          },
        },
      },
    })
    expect(wrapper.text()).toContain('登出')
  })
})
