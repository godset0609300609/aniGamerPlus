/**
 * Unit tests for BtView.vue — tab <-> route.query.tab sync.
 *
 * The three child tabs (FiltersTab, FeedsTab, EntriesTab) all fetch data
 * onMounted, and the shared ElTabs stub renders every tab-pane's content
 * unconditionally (no v-if on panes), so every child mounts regardless of
 * which tab is "active". BtApi is fully stubbed so none of that throws.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import {
  createElementPlusStubs,
  elementPlusModuleMock,
} from '../../helpers/elementPlusStubs'

const mockListFeeds = vi.fn()
const mockListFilters = vi.fn()
const mockListEntries = vi.fn()
const mockSearchEntries = vi.fn()
const mockFilterMatchCount = vi.fn()
const mockReplaceFilters = vi.fn()
const mockCreateFeed = vi.fn()
const mockUpdateFeed = vi.fn()
const mockDeleteFeed = vi.fn()
const mockProbeFeed = vi.fn()

vi.mock('@/api/bt', () => ({
  BtApi: vi.fn().mockImplementation(() => ({
    listFeeds: mockListFeeds,
    listFilters: mockListFilters,
    listEntries: mockListEntries,
    searchEntries: mockSearchEntries,
    filterMatchCount: mockFilterMatchCount,
    replaceFilters: mockReplaceFilters,
    createFeed: mockCreateFeed,
    updateFeed: mockUpdateFeed,
    deleteFeed: mockDeleteFeed,
    probeFeed: mockProbeFeed,
  })),
}))

const mockPush = vi.fn()
const mockReplace = vi.fn()
const mockRoute: { query: Record<string, string | undefined> } = { query: {} }
vi.mock('vue-router', () => ({
  useRoute: () => mockRoute,
  useRouter: () => ({ push: mockPush, replace: mockReplace }),
}))

vi.mock('element-plus', () =>
  elementPlusModuleMock({
    ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
    ElMessageBox: { confirm: vi.fn(), alert: vi.fn(), prompt: vi.fn() },
  }),
)

import BtView from '@/views/BtView.vue'

const stubs = createElementPlusStubs()

function mountView() {
  return mount(BtView, { global: { stubs } })
}

beforeEach(() => {
  vi.clearAllMocks()
  mockRoute.query = {}
  mockPush.mockReset()
  mockReplace.mockReset()
  mockListFeeds.mockResolvedValue([])
  mockListFilters.mockResolvedValue([])
  mockListEntries.mockResolvedValue({ items: [], total: 0, page: 1, size: 50 })
  mockSearchEntries.mockResolvedValue([])
  mockFilterMatchCount.mockResolvedValue({ count: 0, over_cap: false })
  mockReplaceFilters.mockResolvedValue({ status: 'ok' })
})

describe('BtView — tab <-> route.query.tab sync', () => {
  it('activates the entries tab when route.query.tab is "entries" on mount', async () => {
    mockRoute.query = { tab: 'entries' }
    const wrapper = mountView()
    await flushPromises()

    const entriesNav = wrapper.find('.el-tabs__item[data-name="entries"]')
    expect(entriesNav.exists()).toBe(true)
    expect(entriesNav.classes()).toContain('is-active')

    const filtersNav = wrapper.find('.el-tabs__item[data-name="filters"]')
    expect(filtersNav.classes()).not.toContain('is-active')
  })

  it('defaults to the filters tab when route.query.tab is absent', async () => {
    const wrapper = mountView()
    await flushPromises()

    const filtersNav = wrapper.find('.el-tabs__item[data-name="filters"]')
    expect(filtersNav.exists()).toBe(true)
    expect(filtersNav.classes()).toContain('is-active')
  })

  it('switching to entries tab pushes ?tab=entries to URL', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('.el-tabs__item[data-name="entries"]').trigger('click')
    await flushPromises()

    expect(mockReplace).toHaveBeenCalledWith({ path: '/bt', query: { tab: 'entries' } })
  })

  it('switching to feeds tab pushes ?tab=feeds to URL', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('.el-tabs__item[data-name="feeds"]').trigger('click')
    await flushPromises()

    expect(mockReplace).toHaveBeenCalledWith({ path: '/bt', query: { tab: 'feeds' } })
  })

  it('switching to filters tab pushes ?tab=filters to URL', async () => {
    mockRoute.query = { tab: 'entries' }
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('.el-tabs__item[data-name="filters"]').trigger('click')
    await flushPromises()

    expect(mockReplace).toHaveBeenCalledWith({ path: '/bt', query: { tab: 'filters' } })
  })

  it('does not replace URL when query already matches active tab', async () => {
    mockRoute.query = { tab: 'entries' }
    mountView()
    await flushPromises()

    expect(mockReplace).not.toHaveBeenCalled()
  })
})
