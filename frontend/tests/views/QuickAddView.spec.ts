/**
 * Unit tests for QuickAddView.vue — the popup landing page opened by the
 * Tampermonkey userscript / bookmarklet from a 動畫瘋 anime page.
 *
 * Strategy:
 *  - Stub `useRoute` (vue-router) with a mutable `query` object.
 *  - Stub AnimeListApi so no real HTTP calls happen.
 *  - Stub `useAuthStore` to toggle admin / non-admin.
 *  - Spy on `window.close` since the view calls it after a successful save.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import {
  createElementPlusStubs,
  elementPlusModuleMock,
} from '../helpers/elementPlusStubs'
import type { AnimeListEntry } from '@/types'

// ---------------------------------------------------------------------------
// vue-router stub — mutable query object per test.
// ---------------------------------------------------------------------------
const mockRoute = { query: {} as Record<string, string | undefined> }
vi.mock('vue-router', () => ({
  useRoute: () => mockRoute,
  useRouter: () => ({ push: vi.fn() }),
}))

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
// AnimeListApi stub.
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
const { mockElMessageSuccess, mockElMessageError } = vi.hoisted(() => ({
  mockElMessageSuccess: vi.fn(),
  mockElMessageError: vi.fn(),
}))

vi.mock('element-plus', () =>
  elementPlusModuleMock({
    ElMessage: {
      success: mockElMessageSuccess,
      error: mockElMessageError,
      warning: vi.fn(),
      info: vi.fn(),
    },
  }),
)

// Import component AFTER mocks are set up.
import QuickAddView from '@/views/QuickAddView.vue'

const stubs = createElementPlusStubs()

function makeEntry(overrides: Partial<AnimeListEntry> & { sn: number }): AnimeListEntry {
  return {
    enabled: true,
    bilingual: false,
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
  return mount(QuickAddView, {
    global: { stubs },
  })
}

let closeSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers()
  mockRoute.query = {}
  isAdminRef.value = false
  userRef.value = { id: 'dl-1', username: 'bob', avatar_url: null, role: 'downloader' }
  mockList.mockResolvedValue({ entries: [] })
  mockReplaceAll.mockResolvedValue({ status: 'ok' })
  closeSpy = vi.spyOn(window, 'close').mockImplementation(() => {})
})

afterEach(() => {
  vi.useRealTimers()
  closeSpy.mockRestore()
})

describe('QuickAddView — dialog auto-open', () => {
  it('opens the dialog automatically when sn query is present', async () => {
    mockRoute.query = { sn: '12345' }

    const wrapper = mountView()
    await flushPromises()

    // The ElDialog stub renders its slot content only while `v-model` is
    // truthy — its presence here proves the dialog opened on mount without
    // any user interaction.
    expect(wrapper.find('form').exists()).toBe(true)
    const snInput = wrapper.find('input[readonly]').element as HTMLInputElement
    expect(snInput.value).toBe('12345')
  })

  it('prefills the name field from the title query param', async () => {
    mockRoute.query = { sn: '12345', title: '進擊的巨人' }

    const wrapper = mountView()
    await flushPromises()

    const nameInput = wrapper.findAll('input').find((i) => i.element.value === '進擊的巨人')
    expect(nameInput).toBeTruthy()
  })
})

describe('QuickAddView — owner_username field admin-only', () => {
  it('hides the owner field for a regular (non-admin) user', async () => {
    isAdminRef.value = false
    mockRoute.query = { sn: '12345' }

    const wrapper = mountView()
    await flushPromises()

    // Only one el-select present (下載模式) when non-admin.
    const selects = wrapper.findAll('.el-select')
    expect(selects).toHaveLength(1)
  })

  it('shows the owner_username select for an admin user', async () => {
    isAdminRef.value = true
    userRef.value = { id: 'admin-1', username: 'alice', avatar_url: null, role: 'admin' }
    mockRoute.query = { sn: '12345' }
    mockList.mockResolvedValue({
      entries: [makeEntry({ sn: 999, owner_id: 'dl-2', owner_username: 'bob' })],
    })

    const wrapper = mountView()
    await flushPromises()

    // Two el-selects present: 下載模式 + 擁有者.
    const selects = wrapper.findAll('.el-select')
    expect(selects).toHaveLength(2)
  })
})

describe('QuickAddView — submit success', () => {
  it('calls AnimeListApi list + replaceAll and closes the window on success', async () => {
    mockRoute.query = { sn: '12345', title: '進擊的巨人' }
    mockList.mockResolvedValue({ entries: [makeEntry({ sn: 1, owner_id: 'dl-1' })] })

    const wrapper = mountView()
    await flushPromises()

    const submitBtn = wrapper.findAll('button').find((b) => b.text().trim() === '加入')!
    await submitBtn.trigger('click')
    await flushPromises()

    expect(mockList).toHaveBeenCalled()
    expect(mockReplaceAll).toHaveBeenCalledTimes(1)
    const savedEntries = mockReplaceAll.mock.calls[0][0] as AnimeListEntry[]
    expect(savedEntries).toHaveLength(2)
    expect(savedEntries[1]).toMatchObject({ sn: 12345, custom_name: '進擊的巨人' })

    expect(mockElMessageSuccess).toHaveBeenCalledWith(expect.stringContaining('進擊的巨人'))

    expect(closeSpy).not.toHaveBeenCalled()
    vi.advanceTimersByTime(800)
    expect(closeSpy).toHaveBeenCalledTimes(1)
  })
})

describe('QuickAddView — submit failure', () => {
  it('shows an error and keeps the dialog open when submit fails', async () => {
    mockRoute.query = { sn: '12345' }
    mockReplaceAll.mockRejectedValue(new Error('network error'))

    const wrapper = mountView()
    await flushPromises()

    const submitBtn = wrapper.findAll('button').find((b) => b.text().trim() === '加入')!
    await submitBtn.trigger('click')
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalled()
    vi.advanceTimersByTime(1000)
    expect(closeSpy).not.toHaveBeenCalled()

    // Dialog form should still be present for a retry.
    const retryBtn = wrapper.findAll('button').find((b) => b.text().trim() === '加入')
    expect(retryBtn).toBeTruthy()
  })
})

describe('QuickAddView — missing sn query', () => {
  it('shows the error card when sn query is missing', async () => {
    mockRoute.query = {}

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('此頁面需要從動畫瘋透過擴充啟動')
    // No form fields should be rendered.
    expect(wrapper.find('.el-select').exists()).toBe(false)
  })
})
