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

// ---------------------------------------------------------------------------
// Fix 1: current user's section always at top + alphabetical sort
// ---------------------------------------------------------------------------

describe('AnimeListView — Fix 1: current user section always first', () => {
  it('places current user (non-admin) section first regardless of entry order', async () => {
    isAdminRef.value = false
    userRef.value = { id: 'dl-bob', username: 'Bob', avatar_url: null, role: 'downloader' }

    // Entries arrive alice-first
    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 1001, owner_id: 'admin-alice', owner_username: 'alice' }),
        makeEntry({ sn: 2001, owner_id: 'dl-bob', owner_username: 'Bob' }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    const headers = wrapper.findAll('.ag-user-header')
    expect(headers).toHaveLength(2)
    expect(headers[0].text()).toContain('Bob')
    expect(headers[1].text()).toContain('alice')
  })

  it('places current user (admin) section first among multiple users', async () => {
    isAdminRef.value = true
    userRef.value = { id: 'admin-charlie', username: 'charlie', avatar_url: null, role: 'admin' }

    // Entries arrive: alice, bob, charlie
    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 1001, owner_id: 'u-alice', owner_username: 'alice' }),
        makeEntry({ sn: 2001, owner_id: 'u-bob', owner_username: 'bob' }),
        makeEntry({ sn: 3001, owner_id: 'admin-charlie', owner_username: 'charlie' }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    const headers = wrapper.findAll('.ag-user-header')
    expect(headers).toHaveLength(3)
    expect(headers[0].text()).toContain('charlie')
  })

  it('sorts other users alphabetically (case-insensitive) after self', async () => {
    isAdminRef.value = true
    userRef.value = { id: 'admin-1', username: 'admin', avatar_url: null, role: 'admin' }

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 3001, owner_id: 'u-charlie', owner_username: 'Charlie' }),
        makeEntry({ sn: 2001, owner_id: 'u-alice', owner_username: 'alice' }),
        makeEntry({ sn: 4001, owner_id: 'u-bob', owner_username: 'Bob' }),
        makeEntry({ sn: 1001, owner_id: 'admin-1', owner_username: 'admin' }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    const headers = wrapper.findAll('.ag-user-header')
    expect(headers).toHaveLength(4)
    // Self (admin) is first
    expect(headers[0].text()).toContain('admin')
    // Others: alice < Bob < Charlie (case-insensitive)
    expect(headers[1].text()).toContain('alice')
    expect(headers[2].text()).toContain('Bob')
    expect(headers[3].text()).toContain('Charlie')
  })

  it('renders （我） badge on own section for both admin and non-admin', async () => {
    for (const role of ['admin', 'downloader'] as const) {
      isAdminRef.value = role === 'admin'
      userRef.value = { id: 'self-id', username: 'selfuser', avatar_url: null, role }

      mockList.mockResolvedValue({
        entries: [
          makeEntry({ sn: 1001, owner_id: 'self-id', owner_username: 'selfuser' }),
          makeEntry({ sn: 2001, owner_id: 'other-id', owner_username: 'otheruser' }),
        ],
      })

      const wrapper = mountView()
      await flushPromises()

      const selfBadge = wrapper.find('.ag-user-self-badge')
      expect(selfBadge.exists(), `expected badge for role=${role}`).toBe(true)
      expect(selfBadge.text()).toContain('我')
    }
  })
})

// ---------------------------------------------------------------------------
// Fix 2: localStorage collapse persistence
// ---------------------------------------------------------------------------

const COLLAPSE_KEY = 'anigamerplus.animelist.collapse'

describe('AnimeListView — Fix 2: localStorage collapse persistence', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('expands all sections by default when no localStorage key exists', async () => {
    isAdminRef.value = true
    userRef.value = { id: 'admin-1', username: 'alice', avatar_url: null, role: 'admin' }

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 1001, owner_id: 'admin-1', owner_username: 'alice', tag: 'default' }),
      ],
    })

    mountView()
    await flushPromises()

    // localStorage should now contain the key with the expanded section
    const raw = localStorage.getItem(COLLAPSE_KEY)
    expect(raw).not.toBeNull()
    const parsed = JSON.parse(raw!) as { open: string[] }
    expect(Array.isArray(parsed.open)).toBe(true)
    // The key 'admin-1::default' should be in the open array
    expect(parsed.open).toContain('admin-1::default')
  })

  it('restores persisted open sections on mount', async () => {
    isAdminRef.value = false
    userRef.value = { id: 'dl-2', username: 'bob', avatar_url: null, role: 'downloader' }

    // Pre-seed localStorage: only 'dl-2::spring' open
    localStorage.setItem(COLLAPSE_KEY, JSON.stringify({ open: ['dl-2::spring'] }))

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 2001, owner_id: 'dl-2', owner_username: 'bob', tag: 'spring' }),
        makeEntry({ sn: 2002, owner_id: 'dl-2', owner_username: 'bob', tag: 'fall' }),
      ],
    })

    mountView()
    await flushPromises()

    // After mount, the persisted state should be preserved (not overwritten with all-open)
    const raw = localStorage.getItem(COLLAPSE_KEY)
    const parsed = JSON.parse(raw!) as { open: string[] }
    expect(parsed.open).toContain('dl-2::spring')
    // 'fall' was not in the persisted state so it stays collapsed
    expect(parsed.open).not.toContain('dl-2::fall')
  })

  it('saves updated collapse state to localStorage when activeSections changes', async () => {
    isAdminRef.value = false
    userRef.value = { id: 'dl-2', username: 'bob', avatar_url: null, role: 'downloader' }

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 2001, owner_id: 'dl-2', owner_username: 'bob', tag: 'spring' }),
        makeEntry({ sn: 2002, owner_id: 'dl-2', owner_username: 'bob', tag: 'fall' }),
      ],
    })

    // No pre-seeded localStorage — component will expand all on first load.
    mountView()
    await flushPromises()

    // After data load, the watcher fires and writes the current open array to localStorage.
    const raw = localStorage.getItem(COLLAPSE_KEY)
    expect(raw).not.toBeNull()
    const parsed = JSON.parse(raw!) as { open: string[] }
    expect(Array.isArray(parsed.open)).toBe(true)
    // Both tag groups should appear as open (first-time = all-expanded behaviour)
    expect(parsed.open).toContain('dl-2::spring')
    expect(parsed.open).toContain('dl-2::fall')
  })

  it('falls back to empty array on malformed localStorage JSON', async () => {
    isAdminRef.value = false
    userRef.value = { id: 'dl-2', username: 'bob', avatar_url: null, role: 'downloader' }

    // Tampered JSON
    localStorage.setItem(COLLAPSE_KEY, 'not-valid-json{{')

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 2001, owner_id: 'dl-2', owner_username: 'bob', tag: '' }),
      ],
    })

    // Should not throw
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.exists()).toBe(true)
    // After load (no persisted = all expanded), the key should be written with a valid array
    const raw = localStorage.getItem(COLLAPSE_KEY)
    expect(raw).not.toBeNull()
    const parsed = JSON.parse(raw!) as { open: string[] }
    expect(Array.isArray(parsed.open)).toBe(true)
  })

  it('falls back gracefully when localStorage value has wrong shape', async () => {
    isAdminRef.value = false
    userRef.value = { id: 'dl-2', username: 'bob', avatar_url: null, role: 'downloader' }

    // Valid JSON but wrong shape (no 'open' array)
    localStorage.setItem(COLLAPSE_KEY, JSON.stringify({ wrong: 'shape' }))

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 2001, owner_id: 'dl-2', owner_username: 'bob' }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    // Should render without crashing and expand all (fallback behaviour)
    expect(wrapper.exists()).toBe(true)
    const raw = localStorage.getItem(COLLAPSE_KEY)
    const parsed = JSON.parse(raw!) as { open: string[] }
    expect(Array.isArray(parsed.open)).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Truncation + tooltip on long anime names
// ---------------------------------------------------------------------------

describe('AnimeListView — long anime_name: tooltip + truncation', () => {
  it('renders long anime_name inside el-tooltip with matching content prop', async () => {
    isAdminRef.value = true
    userRef.value = { id: 'admin-1', username: 'alice', avatar_url: null, role: 'admin' }

    const longName = 'Super Long Bangumi Name That Exceeds Fifty Characters For Truncation Test'
    expect(longName.length).toBeGreaterThan(50)

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 1001, owner_id: 'admin-1', owner_username: 'alice', anime_name: longName }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    // The el-tooltip stub renders as `.el-tooltip` with data-content attribute.
    const tooltip = wrapper.find('.el-tooltip[data-content]')
    expect(tooltip.exists()).toBe(true)
    expect(tooltip.attributes('data-content')).toBe(longName)
  })

  it('renders anime_name text inside ag-truncate span within the tooltip', async () => {
    isAdminRef.value = false
    userRef.value = { id: 'dl-2', username: 'bob', avatar_url: null, role: 'downloader' }

    const longName = 'Super Long Anime Name That Should Be Truncated In The Table Cell Display'
    expect(longName.length).toBeGreaterThan(50)

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 2001, owner_id: 'dl-2', owner_username: 'bob', anime_name: longName }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    const truncateSpan = wrapper.find('.ag-truncate')
    expect(truncateSpan.exists()).toBe(true)
    expect(truncateSpan.text()).toBe(longName)
  })

  it('does NOT render tooltip or ag-truncate span when anime_name is null', async () => {
    isAdminRef.value = false
    userRef.value = { id: 'dl-2', username: 'bob', avatar_url: null, role: 'downloader' }

    mockList.mockResolvedValue({
      entries: [
        makeEntry({ sn: 2001, owner_id: 'dl-2', owner_username: 'bob', anime_name: null }),
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('.ag-truncate').exists()).toBe(false)
    // The muted placeholder should show instead.
    expect(wrapper.find('.ag-muted').exists()).toBe(true)
  })
})
