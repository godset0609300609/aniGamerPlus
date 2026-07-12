/**
 * Unit tests for EntriesTab.vue — paginated/searchable bt_feed_entry listing
 * plus the "匯入過濾器" shortcut into FiltersImportDialog.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import {
  createElementPlusStubs,
  elementPlusModuleMock,
} from '../../helpers/elementPlusStubs'
import type { BtEntriesPage, BtFeed, BtFeedEntry, BtFilter } from '@/types'

const mockListEntries = vi.fn()
const mockListFeeds = vi.fn()
const mockListFilters = vi.fn()
const mockSearchEntries = vi.fn()
const mockFilterMatchCount = vi.fn()
const mockReplaceFilters = vi.fn()
const mockDispatchEntry = vi.fn()

vi.mock('@/api/bt', () => ({
  BtApi: vi.fn().mockImplementation(() => ({
    listEntries: mockListEntries,
    listFeeds: mockListFeeds,
    listFilters: mockListFilters,
    searchEntries: mockSearchEntries,
    filterMatchCount: mockFilterMatchCount,
    replaceFilters: mockReplaceFilters,
    dispatchEntry: mockDispatchEntry,
  })),
}))

const mockRoute = { query: {} as Record<string, string | undefined> }
vi.mock('vue-router', () => ({
  useRoute: () => mockRoute,
  useRouter: () => ({ push: vi.fn() }),
}))

// ---------------------------------------------------------------------------
// useBreakpoint stub — controllable isMobile so the card-mode switch can be
// tested without real matchMedia/viewport plumbing.
// ---------------------------------------------------------------------------
const isMobileRef = ref(false)

vi.mock('@/composables/useBreakpoint', () => ({
  useBreakpoint: () => ({
    isMobile: isMobileRef,
    isTablet: ref(false),
  }),
}))

const { mockElMessageError, mockElMessageSuccess, mockElMessageBoxConfirm } = vi.hoisted(() => ({
  mockElMessageError: vi.fn(),
  mockElMessageSuccess: vi.fn(),
  mockElMessageBoxConfirm: vi.fn(),
}))

vi.mock('element-plus', () =>
  elementPlusModuleMock({
    ElMessage: { success: mockElMessageSuccess, error: mockElMessageError, warning: vi.fn(), info: vi.fn() },
    ElMessageBox: { confirm: mockElMessageBoxConfirm, alert: vi.fn(), prompt: vi.fn() },
  }),
)

import EntriesTab from '@/views/bt/EntriesTab.vue'

const stubs = createElementPlusStubs()

function makeFeed(overrides: Partial<BtFeed> = {}): BtFeed {
  return {
    id: 1,
    name: 'dmhy 動畫',
    url: 'https://example.com/rss.xml',
    title_key: 'title',
    link_key: 'link',
    guid_key: 'guid',
    author_key: null,
    enabled: true,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    entry_count: 0,
    ...overrides,
  }
}

function makeFilter(overrides: Partial<BtFilter> = {}): BtFilter {
  return {
    id: 1,
    name: 'LoliHouse 1080',
    keywords: ['LoliHouse'],
    enabled: true,
    sort_order: 0,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    ...overrides,
  }
}

function makeEntry(overrides: Partial<BtFeedEntry> = {}): BtFeedEntry {
  return {
    id: 1,
    feed_id: 1,
    guid: 'guid-1',
    title: '[LoliHouse] Example - 01 [1080p]',
    link: 'magnet:?xt=urn:btih:example',
    author: null,
    published_at: '2026-01-01T00:00:00+00:00',
    fetched_at: '2026-01-01T00:05:00+00:00',
    matched_filter_id: 1,
    dispatched_at: '2026-01-01T00:05:01+00:00',
    putio_transfer_id: 42,
    putio_status: 'IN_QUEUE',
    local_path: null,
    remote_cleared_at: null,
    ...overrides,
  }
}

function makePage(overrides: Partial<BtEntriesPage> = {}): BtEntriesPage {
  return {
    items: [],
    total: 0,
    page: 1,
    size: 50,
    ...overrides,
  }
}

function mountView() {
  return mount(EntriesTab, { global: { stubs } })
}

beforeEach(() => {
  vi.clearAllMocks()
  mockRoute.query = {}
  mockListEntries.mockResolvedValue(makePage())
  mockListFeeds.mockResolvedValue([makeFeed()])
  mockListFilters.mockResolvedValue([makeFilter()])
  mockSearchEntries.mockResolvedValue([])
  mockFilterMatchCount.mockResolvedValue({ count: 0, over_cap: false })
  mockReplaceFilters.mockResolvedValue({ status: 'ok' })
  mockDispatchEntry.mockResolvedValue({ transfer_id: 100, status: 'IN_QUEUE' })
  mockElMessageBoxConfirm.mockResolvedValue(undefined)
  isMobileRef.value = false
})

describe('EntriesTab — loading', () => {
  it('calls listEntries(7, undefined, 1, 50, undefined, undefined), listFeeds and listFilters on mount', async () => {
    mountView()
    await flushPromises()

    expect(mockListEntries).toHaveBeenCalledWith(7, undefined, 1, 50, undefined, undefined)
    expect(mockListFeeds).toHaveBeenCalledTimes(1)
    expect(mockListFilters).toHaveBeenCalledTimes(1)
  })

  it('shows the empty-state message when there are no entries', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('沒有符合條件的抓取紀錄')
  })

  it('shows an error message when listEntries rejects', async () => {
    mockListEntries.mockRejectedValue(new Error('boom'))
    const wrapper = mountView()
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalledWith(expect.stringContaining('boom'))
    expect(wrapper.exists()).toBe(true)
  })
})

describe('EntriesTab — row rendering', () => {
  it('resolves feed name and matched filter name from id', async () => {
    mockListEntries.mockResolvedValue(makePage({ items: [makeEntry({ feed_id: 1, matched_filter_id: 1 })], total: 1 }))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('dmhy 動畫')
    expect(wrapper.text()).toContain('LoliHouse 1080')
  })

  it('shows a dash for matched_filter_id null (not matched)', async () => {
    mockListEntries.mockResolvedValue(
      makePage({ items: [makeEntry({ matched_filter_id: null, putio_status: null })], total: 1 }),
    )
    const wrapper = mountView()
    await flushPromises()

    const row = wrapper.find('.el-table-row')
    expect(row.text()).toContain('—')
  })

  it('renders the local_path when present', async () => {
    mockListEntries.mockResolvedValue(makePage({ items: [makeEntry({ local_path: '番劇/Example/01.mp4' })], total: 1 }))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('番劇/Example/01.mp4')
  })

  it('renders 未派送 plain text when putio_status is null', async () => {
    mockListEntries.mockResolvedValue(makePage({ items: [makeEntry({ putio_status: null })], total: 1 }))
    const wrapper = mountView()
    await flushPromises()

    const row = wrapper.find('.el-table-row')
    expect(row.text()).toContain('未派送')
    expect(row.find('.el-tag').exists()).toBe(false)
  })
})

describe('EntriesTab — 收錄時間 column', () => {
  it('renders 收錄時間 column with relative time', async () => {
    const fetchedAt = new Date(Date.now() - 9 * 60 * 1000).toISOString()
    mockListEntries.mockResolvedValue(makePage({ items: [makeEntry({ fetched_at: fetchedAt })], total: 1 }))
    const wrapper = mountView()
    await flushPromises()

    const cell = wrapper.find('td[data-label="收錄時間"]')
    expect(cell.exists()).toBe(true)
    expect(cell.text()).not.toContain('開始於')
    expect(cell.text()).toContain('分鐘前')
  })

  it('收錄時間 shows absolute time in tooltip', async () => {
    const fetchedAt = '2026-07-10T21:56:19+00:00'
    mockListEntries.mockResolvedValue(makePage({ items: [makeEntry({ fetched_at: fetchedAt })], total: 1 }))
    const wrapper = mountView()
    await flushPromises()

    const cell = wrapper.find('td[data-label="收錄時間"]')
    const tooltip = cell.find('.el-tooltip')
    expect(tooltip.exists()).toBe(true)
    expect(tooltip.attributes('data-content')).toBe(fetchedAt)
  })
})

describe('EntriesTab — status color mapping', () => {
  it.each([
    ['IN_QUEUE', 'info', '排隊中'],
    ['WAITING', 'info', '等待中'],
    ['PREPARING_DOWNLOAD', 'warning', '準備中'],
    ['DOWNLOADING', 'warning', '下載中'],
    ['COMPLETING', 'warning', '完成中'],
    ['SEEDING', 'primary', '做種中'],
    ['COMPLETED', 'success', '已完成'],
    ['ERROR', 'danger', '失敗'],
  ])('putio_status=%s renders el-tag type=%s label=%s', async (status, expectedType, expectedLabel) => {
    mockListEntries.mockResolvedValue(makePage({ items: [makeEntry({ putio_status: status })], total: 1 }))
    const wrapper = mountView()
    await flushPromises()

    const tag = wrapper.find('.el-tag')
    expect(tag.exists()).toBe(true)
    expect(tag.attributes('data-type')).toBe(expectedType)
    expect(tag.text()).toBe(expectedLabel)
  })
})

describe('EntriesTab — post-landing remote-cleanup status tags', () => {
  it('renders 遠端已清理 tag with a tooltip explaining it', async () => {
    mockListEntries.mockResolvedValue(
      makePage({ items: [makeEntry({ putio_status: '遠端已清理', local_path: '番劇/Example/01.mp4' })], total: 1 }),
    )
    const wrapper = mountView()
    await flushPromises()

    // Scope to the "Put.io 狀態" cell — the row also contains the
    // always-tooltipped 標題/收錄時間 columns, so a bare `.find('.el-tooltip')`
    // on the whole row would grab the wrong one.
    const cell = wrapper.find('td[data-label="Put.io 狀態"]')
    const tag = cell.find('.el-tag')
    expect(tag.exists()).toBe(true)
    expect(tag.text()).toBe('遠端已清理')
    expect(tag.classes()).toContain('ag-tag-remote-cleared')

    const tooltip = cell.find('.el-tooltip')
    expect(tooltip.exists()).toBe(true)
    expect(tooltip.attributes('data-content')).toBe('已在遠端刪除以節省空間，本地檔案仍在')
  })

  it('renders 遠端已移除 tag as a soft/info tag with a tooltip explaining it', async () => {
    mockListEntries.mockResolvedValue(
      makePage({ items: [makeEntry({ putio_status: '遠端已移除', local_path: '番劇/Example/01.mp4' })], total: 1 }),
    )
    const wrapper = mountView()
    await flushPromises()

    const cell = wrapper.find('td[data-label="Put.io 狀態"]')
    const tag = cell.find('.el-tag')
    expect(tag.exists()).toBe(true)
    expect(tag.text()).toBe('遠端已移除')
    expect(tag.attributes('data-type')).toBe('info')

    const tooltip = cell.find('.el-tooltip')
    expect(tooltip.exists()).toBe(true)
    expect(tooltip.attributes('data-content')).toBe(
      'Put.io 端偵測到檔案不存在（可能被自動清理或使用者手動刪除）',
    )
  })

  it('renders the same remote-cleanup tags on the mobile card', async () => {
    isMobileRef.value = true
    mockListEntries.mockResolvedValue(
      makePage({ items: [makeEntry({ putio_status: '遠端已清理', local_path: '番劇/Example/01.mp4' })], total: 1 }),
    )
    const wrapper = mountView()
    await flushPromises()

    const card = wrapper.find('.ag-entry-card')
    // Index 1, not 0 — the badges row renders the feed-name tag first,
    // then the putio_status tag.
    const tags = card.findAll('.el-tag')
    expect(tags.length).toBe(2)
    const statusTag = tags[1]
    expect(statusTag.text()).toBe('遠端已清理')
    expect(statusTag.classes()).toContain('ag-tag-remote-cleared')
  })

  it('does not wrap ordinary lifecycle statuses (no tooltip mapping) in a tooltip', async () => {
    mockListEntries.mockResolvedValue(makePage({ items: [makeEntry({ putio_status: 'COMPLETED' })], total: 1 }))
    const wrapper = mountView()
    await flushPromises()

    const cell = wrapper.find('td[data-label="Put.io 狀態"]')
    expect(cell.find('.el-tag').exists()).toBe(true)
    expect(cell.find('.el-tooltip').exists()).toBe(false)
  })
})

describe('EntriesTab — status filter dropdown', () => {
  it('renders all 8 lifecycle status options', async () => {
    const wrapper = mountView()
    await flushPromises()

    const statusSelect = wrapper.findAll('select.el-select')[0]
    const options = statusSelect.findAll('option').map((o) => o.attributes('value'))
    expect(options).toEqual(
      expect.arrayContaining([
        'IN_QUEUE',
        'WAITING',
        'PREPARING_DOWNLOAD',
        'DOWNLOADING',
        'COMPLETING',
        'SEEDING',
        'COMPLETED',
        'ERROR',
      ]),
    )
  })

  it('also lists the two post-landing remote-cleanup statuses so they are individually filterable', async () => {
    const wrapper = mountView()
    await flushPromises()

    const statusSelect = wrapper.findAll('select.el-select')[0]
    const options = statusSelect.findAll('option').map((o) => o.attributes('value'))
    expect(options).toEqual(expect.arrayContaining(['遠端已清理', '遠端已移除']))
  })

  it('selecting 遠端已清理 refetches entries with that putioStatus sent to the API', async () => {
    const wrapper = mountView()
    await flushPromises()
    mockListEntries.mockClear()

    const statusSelect = wrapper.findAll('select.el-select')[0]
    await statusSelect.setValue('遠端已清理')
    await flushPromises()

    expect(mockListEntries).toHaveBeenCalledWith(7, undefined, 1, 50, undefined, '遠端已清理')
  })

  it('selecting a status refetches entries with putioStatus sent to the API', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(mockListEntries).toHaveBeenLastCalledWith(7, undefined, 1, 50, undefined, undefined)
    mockListEntries.mockClear()

    const statusSelect = wrapper.findAll('select.el-select')[0]
    await statusSelect.setValue('COMPLETED')
    await flushPromises()

    expect(mockListEntries).toHaveBeenCalledWith(7, undefined, 1, 50, undefined, 'COMPLETED')
  })

  it('selecting the 未派送 option sends the __unassigned__ sentinel as putioStatus', async () => {
    const wrapper = mountView()
    await flushPromises()
    mockListEntries.mockClear()

    const statusSelect = wrapper.findAll('select.el-select')[0]
    await statusSelect.setValue('__unassigned__')
    await flushPromises()

    expect(mockListEntries).toHaveBeenCalledWith(7, undefined, 1, 50, undefined, '__unassigned__')
  })

  it('changing status filter resets to page 1', async () => {
    mockListEntries.mockResolvedValue(makePage({ total: 120 }))
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('.el-pagination-next').trigger('click')
    await flushPromises()
    mockListEntries.mockClear()

    const statusSelect = wrapper.findAll('select.el-select')[0]
    await statusSelect.setValue('COMPLETED')
    await flushPromises()

    expect(mockListEntries).toHaveBeenCalledWith(7, undefined, 1, 50, undefined, 'COMPLETED')
  })

  it('a row whose status is outside the current page still renders once returned by the API', async () => {
    // Regression guard for the drowned-completed-row bug: the backend now owns
    // status filtering, so whatever rows it returns are rendered as-is — no
    // client-side re-filtering that could hide a row the server already scoped in.
    mockListEntries.mockResolvedValue(
      makePage({ items: [makeEntry({ id: 9, title: 'Completed item', putio_status: 'COMPLETED' })], total: 1 }),
    )
    const wrapper = mountView()
    await flushPromises()

    const statusSelect = wrapper.findAll('select.el-select')[0]
    await statusSelect.setValue('COMPLETED')
    await flushPromises()

    expect(wrapper.text()).toContain('Completed item')
  })

  it('SEEDING status renders with primary tag color', async () => {
    mockListEntries.mockResolvedValue(makePage({ items: [makeEntry({ putio_status: 'SEEDING' })], total: 1 }))
    const wrapper = mountView()
    await flushPromises()

    const tag = wrapper.find('.el-tag')
    expect(tag.exists()).toBe(true)
    expect(tag.attributes('data-type')).toBe('primary')
    expect(tag.text()).toBe('做種中')
  })
})

describe('EntriesTab — filter_id dropdown', () => {
  it('lists all filters returned by BtApi.listFilters()', async () => {
    mockListFilters.mockResolvedValue([
      makeFilter({ id: 1, name: 'LoliHouse 1080' }),
      makeFilter({ id: 7, name: 'ANi繁' }),
    ])
    const wrapper = mountView()
    await flushPromises()

    const filterSelect = wrapper.findAll('select.el-select')[1]
    const optionValues = filterSelect.findAll('option').map((o) => o.attributes('value'))
    expect(optionValues).toEqual(expect.arrayContaining(['1', '7']))
    expect(wrapper.text()).toContain('LoliHouse 1080')
    expect(wrapper.text()).toContain('ANi繁')
  })

  it('selecting a filter refetches entries with the numeric filter_id', async () => {
    mockListFilters.mockResolvedValue([
      makeFilter({ id: 1, name: 'LoliHouse 1080' }),
      makeFilter({ id: 3, name: 'ANi繁' }),
    ])
    const wrapper = mountView()
    await flushPromises()

    expect(mockListEntries).toHaveBeenLastCalledWith(7, undefined, 1, 50, undefined, undefined)

    const filterSelect = wrapper.findAll('select.el-select')[1]
    await filterSelect.setValue('3')
    await flushPromises()

    expect(mockListEntries).toHaveBeenLastCalledWith(7, 3, 1, 50, undefined, undefined)
  })

  it('pre-selects from route.query.filter on mount when it matches a known filter id', async () => {
    mockRoute.query = { filter: '2' }
    mockListFilters.mockResolvedValue([
      makeFilter({ id: 1, name: 'LoliHouse 1080' }),
      makeFilter({ id: 2, name: 'ANi繁' }),
    ])
    mountView()
    await flushPromises()

    expect(mockListEntries).toHaveBeenLastCalledWith(7, 2, 1, 50, undefined, undefined)
  })

  it('ignores route.query.filter that does not match any known filter id', async () => {
    mockRoute.query = { filter: '999' }
    mockListFilters.mockResolvedValue([makeFilter({ id: 1, name: 'LoliHouse 1080' })])
    mountView()
    await flushPromises()

    expect(mockListEntries).toHaveBeenLastCalledWith(7, undefined, 1, 50, undefined, undefined)
    expect(mockListEntries).not.toHaveBeenCalledWith(7, 999, 1, 50, undefined, undefined)
  })
})

describe('EntriesTab — manual refresh', () => {
  it('重新整理 button re-fetches entries', async () => {
    const wrapper = mountView()
    await flushPromises()

    const callsBefore = mockListEntries.mock.calls.length
    const refreshBtn = wrapper.findAll('button').find((b) => b.text().includes('重新整理'))
    expect(refreshBtn).toBeDefined()
    await refreshBtn!.trigger('click')
    await flushPromises()

    expect(mockListEntries.mock.calls.length).toBeGreaterThan(callsBefore)
  })
})

describe('EntriesTab — search', () => {
  it('renders search input above filters', async () => {
    const wrapper = mountView()
    await flushPromises()

    const toolbar = wrapper.find('.ag-toolbar')
    const searchInput = toolbar.find('input.ag-search-input')
    expect(searchInput.exists()).toBe(true)
    expect(searchInput.attributes('placeholder')).toBe('搜尋標題')

    const children = Array.from(toolbar.element.children)
    const searchIndex = children.indexOf(searchInput.element)
    const firstSelectIndex = children.indexOf(toolbar.findAll('select.el-select')[0].element)
    expect(searchIndex).toBeLessThan(firstSelectIndex)
  })

  it('search input debounced 300ms triggers refetch with q param', async () => {
    vi.useFakeTimers()
    try {
      const wrapper = mountView()
      await flushPromises()
      mockListEntries.mockClear()

      const searchInput = wrapper.find('input.ag-search-input')
      await searchInput.setValue('attack')

      expect(mockListEntries).not.toHaveBeenCalled()

      vi.advanceTimersByTime(299)
      await flushPromises()
      expect(mockListEntries).not.toHaveBeenCalled()

      vi.advanceTimersByTime(1)
      await flushPromises()
      expect(mockListEntries).toHaveBeenCalledWith(7, undefined, 1, 50, 'attack', undefined)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('EntriesTab — pagination', () => {
  it('pagination triggers refetch with page/size', async () => {
    mockListEntries.mockResolvedValue(makePage({ total: 120 }))
    const wrapper = mountView()
    await flushPromises()
    mockListEntries.mockClear()

    const nextBtn = wrapper.find('.el-pagination-next')
    expect(nextBtn.exists()).toBe(true)
    await nextBtn.trigger('click')
    await flushPromises()

    expect(mockListEntries).toHaveBeenCalledWith(7, undefined, 2, 50, undefined, undefined)
  })

  it('changing page size resets to page 1', async () => {
    mockListEntries.mockResolvedValue(makePage({ total: 120 }))
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('.el-pagination-next').trigger('click')
    await flushPromises()
    mockListEntries.mockClear()

    const sizeSelect = wrapper.find('select.el-pagination-sizes')
    expect(sizeSelect.exists()).toBe(true)
    await sizeSelect.setValue('20')
    await flushPromises()

    expect(mockListEntries).toHaveBeenCalledWith(7, undefined, 1, 20, undefined, undefined)
  })
})

describe('EntriesTab — import filter shortcut', () => {
  it('匯入過濾器 button opens FiltersImportDialog with initialEntry', async () => {
    const entry = makeEntry({ id: 42, title: 'Some Title To Import - 01', matched_filter_id: null })
    mockListEntries.mockResolvedValue(makePage({ items: [entry], total: 1 }))
    const wrapper = mountView()
    await flushPromises()

    const importBtn = wrapper.findAll('button').find((b) => b.text().includes('匯入過濾器'))
    expect(importBtn).toBeDefined()
    await importBtn!.trigger('click')
    await flushPromises()

    // FiltersImportDialog skips its search step and jumps to the token
    // preview once initialEntry is pre-populated.
    expect(wrapper.find('input.el-autocomplete').exists()).toBe(false)
    expect(wrapper.text()).toContain(entry.title)
  })

  it('hides 匯入過濾器 for entries already matched to a filter', async () => {
    const entry = makeEntry({ id: 43, title: 'Already Matched - 02', matched_filter_id: 1 })
    mockListEntries.mockResolvedValue(makePage({ items: [entry], total: 1 }))
    const wrapper = mountView()
    await flushPromises()

    const importBtn = wrapper.findAll('button').find((b) => b.text().includes('匯入過濾器'))
    expect(importBtn).toBeUndefined()

    const row = wrapper.find('.el-table-row')
    expect(row.text()).toContain('—')
  })
})

describe('EntriesTab — 派送 Put.io manual dispatch', () => {
  it('派送 button dispatches entry when not previously dispatched', async () => {
    const entry = makeEntry({ id: 5, matched_filter_id: null, putio_transfer_id: null, putio_status: null })
    mockListEntries.mockResolvedValue(makePage({ items: [entry], total: 1 }))
    const wrapper = mountView()
    await flushPromises()
    mockListEntries.mockClear()

    const dispatchBtn = wrapper.findAll('button').find((b) => b.text().includes('派送 Put.io'))
    expect(dispatchBtn).toBeDefined()
    await dispatchBtn!.trigger('click')
    await flushPromises()

    expect(mockElMessageBoxConfirm).not.toHaveBeenCalled()
    expect(mockDispatchEntry).toHaveBeenCalledWith(5)
    expect(mockElMessageSuccess).toHaveBeenCalledWith('已派送至 Put.io')
    expect(mockListEntries).toHaveBeenCalledTimes(1) // table refreshed via fetchEntries()
  })

  it('重新派送 button shows confirmation dialog for already-dispatched entry', async () => {
    const entry = makeEntry({ id: 6, putio_transfer_id: 42 })
    mockListEntries.mockResolvedValue(makePage({ items: [entry], total: 1 }))
    const wrapper = mountView()
    await flushPromises()

    const redispatchBtn = wrapper.findAll('button').find((b) => b.text().includes('重新派送'))
    expect(redispatchBtn).toBeDefined()

    // Confirm resolves -> dispatch proceeds.
    await redispatchBtn!.trigger('click')
    await flushPromises()
    expect(mockElMessageBoxConfirm).toHaveBeenCalledWith(
      '已派送過，確定要再次派送？',
      expect.any(String),
      expect.objectContaining({ type: 'warning' }),
    )
    expect(mockDispatchEntry).toHaveBeenCalledWith(6)

    // Confirm rejects (user cancels) -> dispatch is NOT called.
    mockDispatchEntry.mockClear()
    mockElMessageBoxConfirm.mockRejectedValueOnce('cancel')
    await redispatchBtn!.trigger('click')
    await flushPromises()
    expect(mockDispatchEntry).not.toHaveBeenCalled()
  })

  it('派送 button visible on matched entries too', async () => {
    // Regression guard: the dispatch button must not be gated by matched_filter_id.
    const entry = makeEntry({ id: 7, matched_filter_id: 1, putio_transfer_id: null })
    mockListEntries.mockResolvedValue(makePage({ items: [entry], total: 1 }))
    const wrapper = mountView()
    await flushPromises()

    const dispatchBtn = wrapper.findAll('button').find((b) => b.text().includes('派送 Put.io'))
    expect(dispatchBtn).toBeDefined()
    // 匯入過濾器 is hidden once matched, but 派送 Put.io stays visible.
    const importBtn = wrapper.findAll('button').find((b) => b.text().includes('匯入過濾器'))
    expect(importBtn).toBeUndefined()
  })

  it('shows an error message and does not refetch when dispatchEntry rejects', async () => {
    mockDispatchEntry.mockRejectedValue(new Error('put.io down'))
    const entry = makeEntry({ id: 8, matched_filter_id: null, putio_transfer_id: null })
    mockListEntries.mockResolvedValue(makePage({ items: [entry], total: 1 }))
    const wrapper = mountView()
    await flushPromises()
    mockListEntries.mockClear()

    const dispatchBtn = wrapper.findAll('button').find((b) => b.text().includes('派送 Put.io'))
    await dispatchBtn!.trigger('click')
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalledWith(expect.stringContaining('put.io down'))
    expect(mockListEntries).not.toHaveBeenCalled()
  })
})

describe('EntriesTab — mobile card mode', () => {
  it('renders the el-table (not cards) on desktop', async () => {
    const entry = makeEntry({ id: 9, title: 'Desktop Entry' })
    mockListEntries.mockResolvedValue(makePage({ items: [entry], total: 1 }))
    isMobileRef.value = false

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('.ag-entries-table').exists()).toBe(true)
    expect(wrapper.find('.ag-entries-cards').exists()).toBe(false)
  })

  it('hides the table and renders one stacked card per entry on mobile', async () => {
    const entry = makeEntry({ id: 9, title: 'Mobile Entry', putio_status: 'IN_QUEUE' })
    mockListEntries.mockResolvedValue(makePage({ items: [entry], total: 1 }))
    isMobileRef.value = true

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('.ag-entries-table').exists()).toBe(false)
    const cards = wrapper.findAll('.ag-entry-card')
    expect(cards).toHaveLength(1)
    expect(cards[0].text()).toContain('Mobile Entry')
    expect(cards[0].text()).toContain('dmhy 動畫')
  })

  it('still exposes the dispatch action button on the mobile card', async () => {
    const entry = makeEntry({ id: 10, title: 'Dispatch Me', putio_transfer_id: null, matched_filter_id: null })
    mockListEntries.mockResolvedValue(makePage({ items: [entry], total: 1 }))
    isMobileRef.value = true

    const wrapper = mountView()
    await flushPromises()

    const card = wrapper.find('.ag-entry-card')
    const dispatchBtn = card.findAll('button').find((b) => b.text().includes('派送 Put.io'))
    expect(dispatchBtn).toBeTruthy()

    await dispatchBtn!.trigger('click')
    await flushPromises()
    expect(mockDispatchEntry).toHaveBeenCalledWith(10)
  })
})
