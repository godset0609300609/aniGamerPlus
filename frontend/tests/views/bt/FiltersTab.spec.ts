/**
 * Unit tests for FiltersTab.vue — draft-map + DirtyFab pattern for bt_filter rows.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h, KeepAlive, ref as vueRef } from 'vue'
import {
  createElementPlusStubs,
  elementPlusModuleMock,
} from '../../helpers/elementPlusStubs'
import type { BtFilter } from '@/types'

const mockListFilters = vi.fn()
const mockReplaceFilters = vi.fn()
const mockListFeeds = vi.fn()
const mockSearchEntries = vi.fn()
const mockFilterMatchCount = vi.fn()

vi.mock('@/api/bt', () => ({
  BtApi: vi.fn().mockImplementation(() => ({
    listFilters: mockListFilters,
    replaceFilters: mockReplaceFilters,
    listFeeds: mockListFeeds,
    searchEntries: mockSearchEntries,
    filterMatchCount: mockFilterMatchCount,
  })),
}))

const mockPush = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mockPush }),
}))

const { mockElMessageSuccess, mockElMessageError, mockElMessageBoxConfirm } = vi.hoisted(() => ({
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

import FiltersTab from '@/views/bt/FiltersTab.vue'
import FiltersImportDialog from '@/views/bt/FiltersImportDialog.vue'

const stubs = createElementPlusStubs()

function makeFilter(overrides: Partial<BtFilter> = {}): BtFilter {
  return {
    id: 1,
    name: 'LoliHouse 1080',
    keywords: ['LoliHouse', '1080'],
    enabled: true,
    sort_order: 0,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    ...overrides,
  }
}

const mountStubs = {
  ...stubs,
  DirtyFab: {
    props: ['visible', 'saving'],
    emits: ['save', 'discard'],
    template:
      '<div class="dirty-fab-stub" v-if="visible">' +
      '<button class="dirty-fab-save" @click="$emit(\'save\')">save</button>' +
      '<button class="dirty-fab-discard" @click="$emit(\'discard\')">discard</button>' +
      '</div>',
  },
}

function mountView() {
  return mount(FiltersTab, {
    global: { stubs: mountStubs },
  })
}

/**
 * Mounts FiltersTab behind a real <KeepAlive> boundary so `onActivated`
 * actually fires on reactivation — a bare `mount(FiltersTab)` never fires
 * it (there's no KeepAlive ancestor), which is why `mountView()` above
 * can't be reused for the reactivation tests.
 */
function mountBehindKeepAlive() {
  const showFilters = vueRef(true)
  const Harness = defineComponent({
    setup() {
      return () => h(KeepAlive, null, { default: () => (showFilters.value ? h(FiltersTab) : h('div')) })
    },
  })
  const wrapper = mount(Harness, { global: { stubs: mountStubs } })
  return { wrapper, showFilters }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockPush.mockReset()
  mockListFilters.mockResolvedValue([])
  mockReplaceFilters.mockResolvedValue({ status: 'ok' })
  mockListFeeds.mockResolvedValue([])
  mockSearchEntries.mockResolvedValue([])
  mockFilterMatchCount.mockResolvedValue({ count: 0, over_cap: false })
  mockElMessageBoxConfirm.mockResolvedValue(undefined)
})

describe('FiltersTab — empty state', () => {
  it('renders the empty-state message when there are no filters', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('目前沒有任何過濾器')
  })

  it('calls listFilters on mount', async () => {
    mountView()
    await flushPromises()

    expect(mockListFilters).toHaveBeenCalledTimes(1)
  })
})

describe('FiltersTab — add filter', () => {
  it('appends a blank row when 新增過濾器 is clicked', async () => {
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const addBtn = buttons.find((b) => b.text().includes('新增過濾器'))
    expect(addBtn).toBeDefined()
    await addBtn!.trigger('click')
    await flushPromises()

    const rows = wrapper.findAll('.el-table-row')
    expect(rows).toHaveLength(1)
    expect(wrapper.text()).not.toContain('目前沒有任何過濾器')
  })

  it('marks the form dirty after adding a row', async () => {
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const addBtn = buttons.find((b) => b.text().includes('新增過濾器'))
    await addBtn!.trigger('click')
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.dirty-fab-stub').exists()).toBe(true)
  })
})

describe('FiltersTab — dirty + save', () => {
  it('toggling enabled marks the form dirty', async () => {
    mockListFilters.mockResolvedValue([makeFilter({ id: 1, enabled: true })])
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('.dirty-fab-stub').exists()).toBe(false)

    const toggle = wrapper.find('input.el-switch')
    expect(toggle.exists()).toBe(true)
    await toggle.setValue(false)
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.dirty-fab-stub').exists()).toBe(true)
  })

  it('save calls BtApi.replaceFilters with the current filter list', async () => {
    mockListFilters.mockResolvedValue([makeFilter({ id: 1, name: 'Original' })])
    const wrapper = mountView()
    await flushPromises()

    const nameInput = wrapper.find('input.el-input')
    await nameInput.setValue('Renamed')
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.find('.dirty-fab-save')
    expect(saveBtn.exists()).toBe(true)
    await saveBtn.trigger('click')
    await flushPromises()

    expect(mockReplaceFilters).toHaveBeenCalledTimes(1)
    const savedArg = mockReplaceFilters.mock.calls[0][0] as BtFilter[]
    expect(savedArg).toHaveLength(1)
    expect(savedArg[0].name).toBe('Renamed')
    expect(mockElMessageSuccess).toHaveBeenCalledWith('過濾器已儲存')
  })

  it('shows an error message when replaceFilters rejects', async () => {
    mockListFilters.mockResolvedValue([makeFilter({ id: 1 })])
    mockReplaceFilters.mockRejectedValue(new Error('network error'))
    const wrapper = mountView()
    await flushPromises()

    const toggle = wrapper.find('input.el-switch')
    await toggle.setValue(false)
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.find('.dirty-fab-save')
    await saveBtn.trigger('click')
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalledWith(expect.stringContaining('network error'))
  })
})

describe('FiltersTab — sort order column', () => {
  it('constrains the sort_order input to a bounded width so it fits its column', async () => {
    mockListFilters.mockResolvedValue([makeFilter({ id: 1 })])
    const wrapper = mountView()
    await flushPromises()

    const sortInput = wrapper.find('input.el-input-number')
    expect(sortInput.exists()).toBe(true)
    expect(sortInput.attributes('style')).toContain('width: 90px')
  })
})

describe('FiltersTab — keyword tags', () => {
  it('renders each keyword as a closable tag', async () => {
    mockListFilters.mockResolvedValue([makeFilter({ keywords: ['LoliHouse', '1080', '繁'] })])
    const wrapper = mountView()
    await flushPromises()

    const tags = wrapper.findAll('.el-tag')
    expect(tags.length).toBeGreaterThanOrEqual(3)
    expect(wrapper.text()).toContain('LoliHouse')
    expect(wrapper.text()).toContain('1080')
    expect(wrapper.text()).toContain('繁')
  })

  it('adding a keyword via the "+" input marks the form dirty', async () => {
    mockListFilters.mockResolvedValue([makeFilter({ keywords: [] })])
    const wrapper = mountView()
    await flushPromises()

    const addKeywordBtn = wrapper.find('.ag-keyword-add-btn')
    expect(addKeywordBtn.exists()).toBe(true)
    await addKeywordBtn.trigger('click')
    await wrapper.vm.$nextTick()

    const keywordInput = wrapper.find('input.ag-keyword-input')
    expect(keywordInput.exists()).toBe(true)
    await keywordInput.setValue('新關鍵字')
    await keywordInput.trigger('keyup.enter')
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('新關鍵字')
    expect(wrapper.find('.dirty-fab-stub').exists()).toBe(true)
  })
})

describe('FiltersTab — import from title', () => {
  it('opens the import dialog and appends the emitted filter to the table', async () => {
    const wrapper = mountView()
    await flushPromises()

    const dialog = wrapper.findComponent(FiltersImportDialog)
    expect(dialog.exists()).toBe(true)
    expect(dialog.props('modelValue')).toBe(false)

    const importBtn = wrapper.findAll('button').find((b) => b.text().includes('從標題匯入'))
    expect(importBtn).toBeDefined()
    await importBtn!.trigger('click')
    await flushPromises()

    expect(dialog.props('modelValue')).toBe(true)

    const created: BtFilter = {
      id: -999,
      name: 'LoliHouse',
      keywords: ['LoliHouse', '1080'],
      enabled: true,
      sort_order: 0,
      created_at: '',
      updated_at: '',
    }
    dialog.vm.$emit('filter-created', created)
    await flushPromises()

    const rows = wrapper.findAll('.el-table-row')
    expect(rows).toHaveLength(1)
    expect(wrapper.text()).toContain('LoliHouse')
    expect(wrapper.find('.dirty-fab-stub').exists()).toBe(true)
  })

  it('passes the next available sort_order to the dialog', async () => {
    mockListFilters.mockResolvedValue([
      makeFilter({ id: 1, sort_order: 0 }),
      makeFilter({ id: 2, sort_order: 4 }),
    ])
    const wrapper = mountView()
    await flushPromises()

    const dialog = wrapper.findComponent(FiltersImportDialog)
    expect(dialog.props('nextSortOrder')).toBe(5)
  })
})

describe('FiltersTab — 查看命中 drill-down', () => {
  it('renders a 查看命中 button per row', async () => {
    mockListFilters.mockResolvedValue([
      makeFilter({ id: 1, name: 'LoliHouse' }),
      makeFilter({ id: 2, name: 'ANi繁' }),
    ])
    const wrapper = mountView()
    await flushPromises()

    const rows = wrapper.findAll('.el-table-row')
    expect(rows).toHaveLength(2)
    for (const row of rows) {
      expect(row.text()).toContain('查看命中')
    }
  })

  it('exposes a tooltip explaining the drill-down', async () => {
    mockListFilters.mockResolvedValue([makeFilter({ id: 1, name: 'LoliHouse' })])
    const wrapper = mountView()
    await flushPromises()

    const tooltip = wrapper.find('.el-tooltip')
    expect(tooltip.exists()).toBe(true)
    expect(tooltip.attributes('data-content')).toBe('檢視此過濾器命中的抓取紀錄')
  })

  it('router.push({ path: "/bt", query: { tab: "entries", filter: "<id>" } }) on click', async () => {
    mockListFilters.mockResolvedValue([makeFilter({ id: 7, name: 'ANi繁' })])
    const wrapper = mountView()
    await flushPromises()

    const btn = wrapper.findAll('button').find((b) => b.text().includes('查看命中'))
    expect(btn).toBeDefined()
    await btn!.trigger('click')
    await flushPromises()

    expect(mockPush).toHaveBeenCalledWith({
      path: '/bt',
      query: { tab: 'entries', filter: '7' },
    })
  })
})

// ---------------------------------------------------------------------------
// Fix 5 — BtView keeps every tab alive via <keep-alive>. A filter imported
// from EntriesTab's "匯入過濾器" dialog only shows up here once this tab is
// reactivated, so FiltersTab must refetch on `onActivated` (but never at
// the cost of silently discarding an unsaved edit).
// ---------------------------------------------------------------------------

describe('FiltersTab — onActivated refetch (BtView keep-alive)', () => {
  it('refetches filters when reactivated and there are no unsaved changes', async () => {
    mockListFilters.mockResolvedValue([makeFilter({ id: 1, name: 'Original' })])
    const { wrapper, showFilters } = mountBehindKeepAlive()
    await flushPromises()
    expect(mockListFilters).toHaveBeenCalledTimes(1)
    expect(wrapper.findAll('.el-table-row')).toHaveLength(1)

    // Simulate an import elsewhere adding a new filter server-side. Its
    // keyword ("ImportedKeyword") renders as a tag, unlike its `name`
    // (rendered inside an <el-input>, whose value isn't part of textContent).
    mockListFilters.mockResolvedValue([
      makeFilter({ id: 1, name: 'Original' }),
      makeFilter({ id: 2, name: 'Imported from EntriesTab', keywords: ['ImportedKeyword'] }),
    ])

    showFilters.value = false
    await flushPromises()
    showFilters.value = true
    await flushPromises()

    expect(mockListFilters).toHaveBeenCalledTimes(2)
    expect(wrapper.findAll('.el-table-row')).toHaveLength(2)
    expect(wrapper.text()).toContain('ImportedKeyword')
  })

  it('does NOT refetch (and does not discard the edit) when reactivated with unsaved changes', async () => {
    mockListFilters.mockResolvedValue([makeFilter({ id: 1, name: 'Original' })])
    const { wrapper, showFilters } = mountBehindKeepAlive()
    await flushPromises()

    const nameInput = wrapper.find('input.el-input')
    await nameInput.setValue('Renamed locally, not saved yet')
    await wrapper.vm.$nextTick()
    expect(mockListFilters).toHaveBeenCalledTimes(1)

    showFilters.value = false
    await flushPromises()
    showFilters.value = true
    await flushPromises()

    // No refetch — the unsaved rename must survive the reactivation.
    expect(mockListFilters).toHaveBeenCalledTimes(1)
    const nameInputAfter = wrapper.find('input.el-input')
    expect((nameInputAfter.element as HTMLInputElement).value).toBe('Renamed locally, not saved yet')
  })
})
