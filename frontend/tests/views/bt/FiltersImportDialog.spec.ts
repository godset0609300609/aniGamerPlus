/**
 * Unit tests for FiltersImportDialog.vue — the "從標題匯入" wizard that lets
 * users pick a stored feed entry and tokenize its title into filter
 * keywords. Debounced network calls (search, match-count) are driven with
 * fake timers so the 250ms/500ms delays don't slow the suite down.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import {
  createElementPlusStubs,
  elementPlusModuleMock,
} from '../../helpers/elementPlusStubs'
import type { BtFeed, BtFeedEntry, BtFilter } from '@/types'

const mockSearchEntries = vi.fn()
const mockFilterMatchCount = vi.fn()
const mockListFeeds = vi.fn()
const mockListFilters = vi.fn()
const mockReplaceFilters = vi.fn()

vi.mock('@/api/bt', () => ({
  BtApi: vi.fn().mockImplementation(() => ({
    searchEntries: mockSearchEntries,
    filterMatchCount: mockFilterMatchCount,
    listFeeds: mockListFeeds,
    listFilters: mockListFilters,
    replaceFilters: mockReplaceFilters,
  })),
}))

const { mockElMessageError, mockElMessageSuccess } = vi.hoisted(() => ({
  mockElMessageError: vi.fn(),
  mockElMessageSuccess: vi.fn(),
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

import FiltersImportDialog from '@/views/bt/FiltersImportDialog.vue'

const stubs = createElementPlusStubs()

const RAW_TITLE =
  '[LoliHouse] Hikaru ga Shinda Natsu - 08 [WebRip 1080p HEVC-10bit AAC][CHT&JPN]'
const EXPECTED_KEYWORDS = [
  'LoliHouse',
  'WebRip 1080p HEVC-10bit AAC',
  'CHT&JPN',
  'Hikaru',
  'ga',
  'Shinda',
  'Natsu',
  '08',
]

function makeFeed(overrides: Partial<BtFeed> = {}): BtFeed {
  return {
    id: 1,
    name: 'LoliHouse RSS',
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

function makeEntry(overrides: Partial<BtFeedEntry> = {}): BtFeedEntry {
  return {
    id: 1,
    feed_id: 1,
    guid: 'guid-1',
    title: RAW_TITLE,
    link: 'magnet:?xt=urn:btih:example',
    author: null,
    published_at: '2026-01-01T00:00:00+00:00',
    fetched_at: '2026-01-01T00:05:00+00:00',
    matched_filter_id: null,
    dispatched_at: null,
    putio_transfer_id: null,
    putio_status: null,
    local_path: null,
    remote_cleared_at: null,
    ...overrides,
  }
}

function mountDialog() {
  return mount(FiltersImportDialog, {
    props: { modelValue: false, nextSortOrder: 3 },
    global: { stubs },
  })
}

function mountDialogWithEntry(
  entry: BtFeedEntry,
  mode: 'append-to-draft' | 'save-immediately' = 'append-to-draft',
) {
  return mount(FiltersImportDialog, {
    props: { modelValue: false, nextSortOrder: 3, initialEntry: entry, mode },
    global: { stubs },
  })
}

const EXISTING_FILTER: BtFilter = {
  id: 1,
  name: 'Existing',
  keywords: ['x'],
  enabled: true,
  sort_order: 0,
  created_at: '2026-01-01T00:00:00+00:00',
  updated_at: '2026-01-01T00:00:00+00:00',
}

async function openDialog(wrapper: ReturnType<typeof mountDialog>) {
  await wrapper.setProps({ modelValue: true })
  await flushPromises()
}

async function searchAndSelect(wrapper: ReturnType<typeof mountDialog>) {
  const input = wrapper.find('input.el-autocomplete')
  await input.setValue('LoliHouse')
  vi.advanceTimersByTime(250)
  await flushPromises()

  const suggestionItem = wrapper.find('.el-autocomplete-suggestion-item')
  await suggestionItem.trigger('click')
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers()
  mockListFeeds.mockResolvedValue([makeFeed()])
  mockSearchEntries.mockResolvedValue([makeEntry()])
  mockFilterMatchCount.mockResolvedValue({ count: 5, over_cap: false })
  mockListFilters.mockResolvedValue([])
  mockReplaceFilters.mockResolvedValue({ status: 'ok' })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('FiltersImportDialog — search', () => {
  it('renders the search input', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)

    expect(wrapper.find('input.el-autocomplete').exists()).toBe(true)
  })

  it('calls searchEntries only after the 250ms debounce elapses', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)

    const input = wrapper.find('input.el-autocomplete')
    await input.setValue('LoliHouse')

    expect(mockSearchEntries).not.toHaveBeenCalled()

    vi.advanceTimersByTime(249)
    await flushPromises()
    expect(mockSearchEntries).not.toHaveBeenCalled()

    vi.advanceTimersByTime(1)
    await flushPromises()
    expect(mockSearchEntries).toHaveBeenCalledWith('LoliHouse', 20)
  })
})

describe('FiltersImportDialog — token preview', () => {
  it('picking an entry populates chip groups per tokenizeTitle', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)
    await searchAndSelect(wrapper)

    const tags = wrapper.findAll('.el-tag')
    expect(tags).toHaveLength(EXPECTED_KEYWORDS.length)
    for (const keyword of EXPECTED_KEYWORDS) {
      expect(wrapper.text()).toContain(keyword)
    }
  })

  it('removing a chip removes it from the working set', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)
    await searchAndSelect(wrapper)

    const before = wrapper.findAll('.el-tag').length
    await wrapper.find('.el-tag__close').trigger('click')
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.el-tag').length).toBe(before - 1)
  })

  it('全部清空 clears every chip in that group', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)
    await searchAndSelect(wrapper)

    const clearButtons = wrapper
      .findAll('button')
      .filter((b) => b.text().includes('全部清空'))
    expect(clearButtons.length).toBe(3)

    await clearButtons[0].trigger('click')
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.el-tag')).toHaveLength(5)
  })
})

describe('FiltersImportDialog — manual keywords', () => {
  async function addManualTokenViaEnter(
    wrapper: ReturnType<typeof mountDialog>,
    value: string,
  ) {
    const input = wrapper.find('input.ag-manual-input')
    await input.setValue(value)
    await input.trigger('keyup.enter')
    await wrapper.vm.$nextTick()
  }

  function findManualAddButton(wrapper: ReturnType<typeof mountDialog>) {
    return wrapper.findAll('button').find((b) => b.text().includes('新增'))
  }

  it('manual keyword input appends chip on Enter', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)
    await searchAndSelect(wrapper)

    const before = wrapper.findAll('.el-tag').length
    await addManualTokenViaEnter(wrapper, 'myKeyword')

    expect(wrapper.findAll('.el-tag').length).toBe(before + 1)
    expect(wrapper.text()).toContain('myKeyword')
  })

  it('manual keyword input appends chip on button click', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)
    await searchAndSelect(wrapper)

    const before = wrapper.findAll('.el-tag').length
    const input = wrapper.find('input.ag-manual-input')
    await input.setValue('clickAdded')

    const addBtn = findManualAddButton(wrapper)
    expect(addBtn).toBeDefined()
    await addBtn!.trigger('click')
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.el-tag').length).toBe(before + 1)
    expect(wrapper.text()).toContain('clickAdded')
  })

  it('manual keyword input trims whitespace and rejects empty', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)
    await searchAndSelect(wrapper)

    const before = wrapper.findAll('.el-tag').length

    await addManualTokenViaEnter(wrapper, '   ')
    expect(wrapper.findAll('.el-tag').length).toBe(before)

    await addManualTokenViaEnter(wrapper, '  spaced  ')
    expect(wrapper.findAll('.el-tag').length).toBe(before + 1)
    expect(wrapper.text()).toContain('spaced')
    expect(wrapper.text()).not.toContain('  spaced  ')
  })

  it('manual keyword input rejects duplicate within manual group', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)
    await searchAndSelect(wrapper)

    await addManualTokenViaEnter(wrapper, 'dupToken')
    const afterFirst = wrapper.findAll('.el-tag').length

    await addManualTokenViaEnter(wrapper, 'dupToken')
    expect(wrapper.findAll('.el-tag').length).toBe(afterFirst)
  })

  it('removing a manual chip removes it from the working set', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)
    await searchAndSelect(wrapper)

    await addManualTokenViaEnter(wrapper, 'removableToken')
    const before = wrapper.findAll('.el-tag').length

    const closeButtons = wrapper.findAll('.el-tag__close')
    await closeButtons[closeButtons.length - 1].trigger('click')
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.el-tag').length).toBe(before - 1)
    expect(wrapper.text()).not.toContain('removableToken')
  })

  it('全部清空 in manual group clears every manual chip', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)
    await searchAndSelect(wrapper)

    await addManualTokenViaEnter(wrapper, 'manualOne')
    await addManualTokenViaEnter(wrapper, 'manualTwo')
    const before = wrapper.findAll('.el-tag').length

    const clearButtons = wrapper
      .findAll('button')
      .filter((b) => b.text().includes('全部清空'))
    expect(clearButtons.length).toBe(3)

    await clearButtons[2].trigger('click')
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.el-tag').length).toBe(before - 2)
    expect(wrapper.text()).not.toContain('manualOne')
    expect(wrapper.text()).not.toContain('manualTwo')
  })

  it('manual tokens are included in the emitted filter\'s keywords array in bracket → freeText → manual order', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)
    await searchAndSelect(wrapper)

    await addManualTokenViaEnter(wrapper, 'myManualToken')
    vi.advanceTimersByTime(500)
    await flushPromises()

    const confirmBtn = wrapper.findAll('button').find((b) => b.text().includes('確定匯入'))
    expect(confirmBtn).toBeDefined()
    await confirmBtn!.trigger('click')
    await flushPromises()

    const emitted = wrapper.emitted('filter-created')
    expect(emitted).toBeTruthy()
    const filter = emitted![0][0] as BtFilter
    expect(filter.keywords).toEqual([...EXPECTED_KEYWORDS, 'myManualToken'])
  })

  it('filterMatchCount fires when a manual chip is added', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)
    await searchAndSelect(wrapper)

    vi.advanceTimersByTime(500)
    await flushPromises()
    mockFilterMatchCount.mockClear()

    await addManualTokenViaEnter(wrapper, 'watchedToken')

    expect(mockFilterMatchCount).not.toHaveBeenCalled()
    vi.advanceTimersByTime(500)
    await flushPromises()

    expect(mockFilterMatchCount).toHaveBeenCalledWith([...EXPECTED_KEYWORDS, 'watchedToken'])
  })
})

describe('FiltersImportDialog — naming', () => {
  it('defaults the name field to the first bracket token', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)
    await searchAndSelect(wrapper)

    const nameInput = wrapper.find('input.ag-name-input')
    expect((nameInput.element as HTMLInputElement).value).toBe('LoliHouse')
  })
})

describe('FiltersImportDialog — match count', () => {
  it('calls filterMatchCount 500ms after the token set changes, and again on edits', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)
    await searchAndSelect(wrapper)

    expect(mockFilterMatchCount).not.toHaveBeenCalled()
    vi.advanceTimersByTime(500)
    await flushPromises()
    expect(mockFilterMatchCount).toHaveBeenCalledWith(EXPECTED_KEYWORDS)

    mockFilterMatchCount.mockClear()
    await wrapper.find('.el-tag__close').trigger('click')
    await wrapper.vm.$nextTick()

    vi.advanceTimersByTime(499)
    await flushPromises()
    expect(mockFilterMatchCount).not.toHaveBeenCalled()

    vi.advanceTimersByTime(1)
    await flushPromises()
    expect(mockFilterMatchCount).toHaveBeenCalledTimes(1)
  })

  it('shows the over-cap note when the backend caps the scan', async () => {
    mockFilterMatchCount.mockResolvedValue({ count: 10000, over_cap: true })
    const wrapper = mountDialog()
    await openDialog(wrapper)
    await searchAndSelect(wrapper)

    vi.advanceTimersByTime(500)
    await flushPromises()

    expect(wrapper.text()).toContain('僅計算最近 10000 筆')
  })
})

describe('FiltersImportDialog — confirm / cancel', () => {
  it('確定匯入 emits filter-created with the correct BtFilter shape and closes', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)
    await searchAndSelect(wrapper)
    vi.advanceTimersByTime(500)
    await flushPromises()

    const confirmBtn = wrapper.findAll('button').find((b) => b.text().includes('確定匯入'))
    expect(confirmBtn).toBeDefined()
    await confirmBtn!.trigger('click')
    await flushPromises()

    const emitted = wrapper.emitted('filter-created')
    expect(emitted).toBeTruthy()
    const filter = emitted![0][0] as BtFilter
    expect(filter.name).toBe('LoliHouse')
    expect(filter.keywords).toEqual(EXPECTED_KEYWORDS)
    expect(filter.enabled).toBe(true)
    expect(filter.sort_order).toBe(3)
    expect(filter.id).toBeLessThan(0)

    const modelUpdates = wrapper.emitted('update:modelValue')
    expect(modelUpdates?.at(-1)?.[0]).toBe(false)
  })

  it('取消 closes without emitting filter-created', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)
    await searchAndSelect(wrapper)

    const cancelBtn = wrapper.findAll('button').find((b) => b.text().includes('取消'))
    expect(cancelBtn).toBeDefined()
    await cancelBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('filter-created')).toBeFalsy()
    const modelUpdates = wrapper.emitted('update:modelValue')
    expect(modelUpdates?.at(-1)?.[0]).toBe(false)
  })
})

describe('FiltersImportDialog — initialEntry prop', () => {
  it('when initialEntry prop is set, dialog skips search and shows tokens immediately', async () => {
    const wrapper = mountDialogWithEntry(makeEntry())
    await openDialog(wrapper)

    expect(wrapper.find('input.el-autocomplete').exists()).toBe(false)

    const tags = wrapper.findAll('.el-tag')
    expect(tags).toHaveLength(EXPECTED_KEYWORDS.length)
    for (const keyword of EXPECTED_KEYWORDS) {
      expect(wrapper.text()).toContain(keyword)
    }
  })
})

describe('FiltersImportDialog — mode', () => {
  it('mode="save-immediately" calls BtApi.replaceFilters on confirm and does not emit filter-created', async () => {
    mockListFilters.mockResolvedValue([EXISTING_FILTER])
    const wrapper = mountDialogWithEntry(makeEntry(), 'save-immediately')
    await openDialog(wrapper)

    const confirmBtn = wrapper.findAll('button').find((b) => b.text().includes('確定匯入'))
    expect(confirmBtn).toBeDefined()
    await confirmBtn!.trigger('click')
    await flushPromises()

    expect(mockListFilters).toHaveBeenCalledTimes(1)
    expect(mockReplaceFilters).toHaveBeenCalledTimes(1)
    const savedList = mockReplaceFilters.mock.calls[0][0] as BtFilter[]
    expect(savedList).toHaveLength(2)
    expect(savedList[0]).toEqual(EXISTING_FILTER)
    expect(savedList[1].keywords).toEqual(EXPECTED_KEYWORDS)
    expect(mockElMessageSuccess).toHaveBeenCalledWith('已新增過濾器')

    expect(wrapper.emitted('filter-created')).toBeFalsy()
    const modelUpdates = wrapper.emitted('update:modelValue')
    expect(modelUpdates?.at(-1)?.[0]).toBe(false)
  })

  it('mode="append-to-draft" (default) emits filter-created and does not call replaceFilters', async () => {
    const wrapper = mountDialogWithEntry(makeEntry())
    await openDialog(wrapper)

    const confirmBtn = wrapper.findAll('button').find((b) => b.text().includes('確定匯入'))
    expect(confirmBtn).toBeDefined()
    await confirmBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('filter-created')).toBeTruthy()
    expect(mockReplaceFilters).not.toHaveBeenCalled()
    expect(mockListFilters).not.toHaveBeenCalled()
  })
})
