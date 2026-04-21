/**
 * Unit tests for AnimeListView.vue — admin grouping and non-admin flat view.
 *
 * Strategy:
 *  - Stub AnimeListApi so no real HTTP calls happen.
 *  - Control `useAuthStore` to toggle admin / non-admin.
 *  - Assert that admin mode renders user-section headers per owner.
 *  - Assert that non-admin mode renders no user-section headers.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import {
  createElementPlusStubs,
  elementPlusModuleMock,
} from '../helpers/elementPlusStubs'
import type { AnimeListEntry } from '@/types'

// ---------------------------------------------------------------------------
// Auth store stub — controllable role.
// ---------------------------------------------------------------------------
const isAdminRef = ref(false)
const userRef = ref<{ id: string; username: string; avatar_url: null; role: string } | null>(null)

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({
    isAdmin: isAdminRef,
    user: userRef,
  }),
}))

// ---------------------------------------------------------------------------
// AnimeListApi stub — list() returns controllable data.
// ---------------------------------------------------------------------------
const mockList = vi.fn()
const mockReplaceAll = vi.fn()

vi.mock('@/api/animelist', () => ({
  AnimeListApi: vi.fn().mockImplementation(() => ({
    list: mockList,
    replaceAll: mockReplaceAll,
  })),
}))

// ---------------------------------------------------------------------------
// Element Plus mock.
// ---------------------------------------------------------------------------
const { mockElMessageSuccess, mockElMessageError, mockElMessageBoxConfirm } =
  vi.hoisted(() => ({
    mockElMessageSuccess: vi.fn(),
    mockElMessageError: vi.fn(),
    mockElMessageBoxConfirm: vi.fn(),
  }))

vi.mock('element-plus', () =>
  elementPlusModuleMock({
    ElMessage: {
      success: mockElMessageSuccess,
      error: mockElMessageError,
      warning: vi.fn(),
      info: vi.fn(),
    },
    ElMessageBox: {
      confirm: mockElMessageBoxConfirm,
      alert: vi.fn(),
      prompt: vi.fn(),
    },
  }),
)

// Import component AFTER mocks are set up.
import AnimeListView from '@/views/AnimeListView.vue'

const stubs = createElementPlusStubs()

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeEntry(
  overrides: Partial<AnimeListEntry> & { sn: number },
): AnimeListEntry {
  return {
    enabled: true,
    mode: null,
    tag: '',
    season: 1,
    custom_name: null,
    comment: '',
    anime_name: null,
    downloaded_episodes: 0,
    known_episodes: 0,
    owner_id: null,
    owner_username: null,
    ...overrides,
  }
}

function mountView() {
  return mount(AnimeListView, {
    global: {
      stubs: {
        ...stubs,
        DirtyFab: { template: '<div class="dirty-fab-stub" />' },
      },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  isAdminRef.value = false
  userRef.value = null
  mockReplaceAll.mockResolvedValue({ status: 'ok' })
  mockElMessageBoxConfirm.mockResolvedValue(undefined)
})

// ---------------------------------------------------------------------------
// Admin view — grouped by user
// ---------------------------------------------------------------------------

describe('AnimeListView — admin mode: user section headers', () => {
  it('renders a section header for each distinct owner when admin', async () => {
    isAdminRef.value = true
    userRef.value = { id: 'admin-1', username: 'alice', avatar_url: null, role: 'admin' }

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 1001, owner_id: 'admin-1', owner_username: 'alice' }),
        makeEntry({ sn: 2001, owner_id: 'dl-2', owner_username: 'bob' }),
        makeEntry({ sn: 2002, owner_id: 'dl-2', owner_username: 'bob' }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    const headers = wrapper.findAll('.ag-user-header')
    expect(headers).toHaveLength(2)

    const headerTexts = headers.map((h) => h.text())
    expect(headerTexts.some((t) => t.includes('alice'))).toBe(true)
    expect(headerTexts.some((t) => t.includes('bob'))).toBe(true)
  })

  it('marks the admin\'s own section with （我）badge', async () => {
    isAdminRef.value = true
    userRef.value = { id: 'admin-1', username: 'alice', avatar_url: null, role: 'admin' }

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 1001, owner_id: 'admin-1', owner_username: 'alice' }),
        makeEntry({ sn: 2001, owner_id: 'dl-2', owner_username: 'bob' }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    const selfBadge = wrapper.find('.ag-user-self-badge')
    expect(selfBadge.exists()).toBe(true)
    expect(selfBadge.text()).toContain('我')
  })

  it('shows admin\'s own section first', async () => {
    isAdminRef.value = true
    userRef.value = { id: 'admin-1', username: 'alice', avatar_url: null, role: 'admin' }

    // Entries arrive with downloader first in the array.
    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 2001, owner_id: 'dl-2', owner_username: 'bob' }),
        makeEntry({ sn: 1001, owner_id: 'admin-1', owner_username: 'alice' }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    const headers = wrapper.findAll('.ag-user-header')
    expect(headers).toHaveLength(2)
    // First header should be alice (the admin).
    expect(headers[0].text()).toContain('alice')
    expect(headers[1].text()).toContain('bob')
  })

  it('shows entry count badge on each user section', async () => {
    isAdminRef.value = true
    userRef.value = { id: 'admin-1', username: 'alice', avatar_url: null, role: 'admin' }

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 1001, owner_id: 'admin-1', owner_username: 'alice' }),
        makeEntry({ sn: 2001, owner_id: 'dl-2', owner_username: 'bob' }),
        makeEntry({ sn: 2002, owner_id: 'dl-2', owner_username: 'bob' }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    const counts = wrapper.findAll('.ag-user-count')
    expect(counts.some((c) => c.text().includes('1'))).toBe(true)
    expect(counts.some((c) => c.text().includes('2'))).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Non-admin view — Feature A: now renders owner-grouped sections too
// ---------------------------------------------------------------------------

describe('AnimeListView — non-admin mode: owner sections rendered', () => {
  it('renders owner section headers for a non-admin user (Feature A)', async () => {
    isAdminRef.value = false
    userRef.value = { id: 'dl-2', username: 'bob', avatar_url: null, role: 'downloader' }

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 2001, owner_id: 'dl-2', owner_username: 'bob' }),
        makeEntry({ sn: 1001, owner_id: 'admin-1', owner_username: 'alice' }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    // Now non-admin also sees owner section headers.
    const headers = wrapper.findAll('.ag-user-header')
    expect(headers.length).toBeGreaterThanOrEqual(1)
  })

  it('marks own section with （我）badge for non-admin', async () => {
    isAdminRef.value = false
    userRef.value = { id: 'dl-2', username: 'bob', avatar_url: null, role: 'downloader' }

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 2001, owner_id: 'dl-2', owner_username: 'bob' }),
        makeEntry({ sn: 1001, owner_id: 'admin-1', owner_username: 'alice' }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    const selfBadge = wrapper.find('.ag-user-self-badge')
    expect(selfBadge.exists()).toBe(true)
    expect(selfBadge.text()).toContain('我')
  })

  it("renders delete button only on own rows for non-admin", async () => {
    isAdminRef.value = false
    userRef.value = { id: 'dl-2', username: 'bob', avatar_url: null, role: 'downloader' }

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 2001, owner_id: 'dl-2', owner_username: 'bob' }),
        makeEntry({ sn: 1001, owner_id: 'admin-1', owner_username: 'alice' }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    // Only one row is own; the delete button should appear once (for own row only).
    const deleteBtns = wrapper.findAll('button').filter((b) => b.text().trim() === '刪除')
    expect(deleteBtns).toHaveLength(1)
  })

  it('renders tag-grouped collapse items for non-admin', async () => {
    isAdminRef.value = false
    userRef.value = { id: 'dl-2', username: 'bob', avatar_url: null, role: 'downloader' }

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 2001, tag: '冬季番', owner_id: 'dl-2', owner_username: 'bob' }),
        makeEntry({ sn: 2002, tag: '春季番', owner_id: 'dl-2', owner_username: 'bob' }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    // Collapse items (tag groups) should be rendered.
    const items = wrapper.findAll('.el-collapse-item')
    expect(items.length).toBeGreaterThanOrEqual(2)
  })
})

// ---------------------------------------------------------------------------
// Feature B: duplicate row rendering
// ---------------------------------------------------------------------------

describe('AnimeListView — Feature B: duplicate rows', () => {
  it('renders warning icon on duplicate rows', async () => {
    isAdminRef.value = true
    userRef.value = { id: 'admin-1', username: 'alice', avatar_url: null, role: 'admin' }

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 1001, owner_id: 'admin-1', owner_username: 'alice', anime_name: '進擊的巨人' }),
        makeEntry({
          sn: 2001,
          owner_id: 'dl-2',
          owner_username: 'bob',
          anime_name: '進擊的巨人',
          duplicate_of_entry_id: 1,
          duplicate_of_bangumi_name: '進擊的巨人',
          duplicate_of_owner_username: 'alice',
        }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    const dupIcon = wrapper.find('.ag-dup-icon')
    expect(dupIcon.exists()).toBe(true)
  })

  it('does not render warning icon on non-duplicate rows', async () => {
    isAdminRef.value = true
    userRef.value = { id: 'admin-1', username: 'alice', avatar_url: null, role: 'admin' }

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 1001, owner_id: 'admin-1', owner_username: 'alice', anime_name: '不重複番劇' }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('.ag-dup-icon').exists()).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Admin single-user (only their own entries)
// ---------------------------------------------------------------------------

describe('AnimeListView — admin mode: only own entries', () => {
  it('shows one section (self) when admin has only own entries', async () => {
    isAdminRef.value = true
    userRef.value = { id: 'admin-1', username: 'alice', avatar_url: null, role: 'admin' }

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 1001, owner_id: 'admin-1', owner_username: 'alice' }),
        makeEntry({ sn: 1002, owner_id: 'admin-1', owner_username: 'alice' }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    const headers = wrapper.findAll('.ag-user-header')
    expect(headers).toHaveLength(1)
    expect(headers[0].text()).toContain('alice')
    expect(wrapper.find('.ag-user-self-badge').exists()).toBe(true)
  })
})
