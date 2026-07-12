/**
 * Unit tests for FeedsTab.vue — feed table + 3-step add/edit wizard.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import {
  createElementPlusStubs,
  elementPlusModuleMock,
} from '../../helpers/elementPlusStubs'
import type { BtFeed, BtProbeResult } from '@/types'

const mockListFeeds = vi.fn()
const mockCreateFeed = vi.fn()
const mockUpdateFeed = vi.fn()
const mockDeleteFeed = vi.fn()
const mockProbeFeed = vi.fn()

vi.mock('@/api/bt', () => ({
  BtApi: vi.fn().mockImplementation(() => ({
    listFeeds: mockListFeeds,
    createFeed: mockCreateFeed,
    updateFeed: mockUpdateFeed,
    deleteFeed: mockDeleteFeed,
    probeFeed: mockProbeFeed,
  })),
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

import FeedsTab from '@/views/bt/FeedsTab.vue'

const stubs = createElementPlusStubs()

function makeFeed(overrides: Partial<BtFeed> = {}): BtFeed {
  return {
    id: 1,
    name: 'dmhy 動畫',
    url: 'https://share.dmhy.org/topics/rss/sort_id/2/rss.xml',
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

function makeProbeResult(overrides: Partial<BtProbeResult> = {}): BtProbeResult {
  return {
    available_keys: ['title', 'link', 'guid', 'enclosure.url'],
    sample_entries: [
      { title: 'Example 01', link: 'https://example.com/1', guid: 'guid-1' },
      { title: 'Example 02', link: 'https://example.com/2', guid: 'guid-2' },
    ],
    ...overrides,
  }
}

function mountView() {
  return mount(FeedsTab, { global: { stubs } })
}

type FeedsTabVm = {
  step: number
  dialogVisible: boolean
  wizardMode: 'create' | 'edit'
}

interface PreviewColumnWrapper {
  props: (key: 'label') => string
  vm: { $attrs: Record<string, unknown> }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockListFeeds.mockResolvedValue([])
  mockCreateFeed.mockResolvedValue(makeFeed())
  mockUpdateFeed.mockResolvedValue(makeFeed())
  mockDeleteFeed.mockResolvedValue({ status: 'ok' })
  mockProbeFeed.mockResolvedValue(makeProbeResult())
  mockElMessageBoxConfirm.mockResolvedValue(undefined)
})

describe('FeedsTab — wizard steps', () => {
  it('renders three step labels', async () => {
    const wrapper = mountView()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('貼上網址')
    expect(text).toContain('選欄位')
    expect(text).toContain('命名 + 儲存')
  })

  it('starts on step 1 for a new feed', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as FeedsTabVm
    expect(vm.step).toBe(1)
  })
})

describe('FeedsTab — step 1: probe', () => {
  it('shows an error alert when probe fails and stays on step 1', async () => {
    mockProbeFeed.mockRejectedValue(new Error('連線逾時'))
    const wrapper = mountView()
    await flushPromises()

    const urlInput = wrapper.find('input.el-input')
    await urlInput.setValue('https://bad.example/rss.xml')

    const testBtn = wrapper.findAll('button').find((b) => b.text().includes('測試'))
    expect(testBtn).toBeDefined()
    await testBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('測試失敗')
    expect(wrapper.text()).toContain('連線逾時')
    const vm = wrapper.vm as unknown as FeedsTabVm
    expect(vm.step).toBe(1)
  })

  it('advances to step 2 on successful probe', async () => {
    const wrapper = mountView()
    await flushPromises()

    const urlInput = wrapper.find('input.el-input')
    await urlInput.setValue('https://share.dmhy.org/topics/rss/sort_id/2/rss.xml')

    const testBtn = wrapper.findAll('button').find((b) => b.text().includes('測試'))
    await testBtn!.trigger('click')
    await flushPromises()

    expect(mockProbeFeed).toHaveBeenCalledWith(
      'https://share.dmhy.org/topics/rss/sort_id/2/rss.xml',
    )
    const vm = wrapper.vm as unknown as FeedsTabVm
    expect(vm.step).toBe(2)
  })
})

describe('FeedsTab — step 2: field selection + preview', () => {
  async function probeToStep2(wrapper: ReturnType<typeof mountView>): Promise<void> {
    const urlInput = wrapper.find('input.el-input')
    await urlInput.setValue('https://share.dmhy.org/topics/rss/sort_id/2/rss.xml')
    const testBtn = wrapper.findAll('button').find((b) => b.text().includes('測試'))
    await testBtn!.trigger('click')
    await flushPromises()
  }

  it('renders available_keys as select options', async () => {
    const wrapper = mountView()
    await flushPromises()
    await probeToStep2(wrapper)

    const options = wrapper.findAll('select.el-select option')
    const optionValues = options.map((o) => o.attributes('value'))
    expect(optionValues).toEqual(expect.arrayContaining(['title', 'link', 'guid', 'enclosure.url']))
  })

  it('renders preview cards using the currently selected keys', async () => {
    const wrapper = mountView()
    await flushPromises()
    await probeToStep2(wrapper)

    const selects = wrapper.findAll('select.el-select')
    // Order in the template: title_key, link_key, guid_key, author_key.
    await selects[0].setValue('title')
    await selects[1].setValue('link')
    await wrapper.vm.$nextTick()

    const preview = wrapper.find('.ag-preview-list')
    expect(preview.text()).toContain('Example 01')
    expect(preview.text()).toContain('https://example.com/1')
  })

  it('resolves nested dotted keys (e.g. enclosure.url) in the preview', async () => {
    mockProbeFeed.mockResolvedValue(
      makeProbeResult({
        available_keys: ['title', 'enclosure.url', 'guid'],
        sample_entries: [
          { title: 'Nested Example', enclosure: { url: 'magnet:?xt=urn:btih:nested' }, guid: 'g1' },
        ],
      }),
    )
    const wrapper = mountView()
    await flushPromises()
    await probeToStep2(wrapper)

    const selects = wrapper.findAll('select.el-select')
    await selects[0].setValue('title')
    await selects[1].setValue('enclosure.url')
    await wrapper.vm.$nextTick()

    const preview = wrapper.find('.ag-preview-list')
    expect(preview.text()).toContain('magnet:?xt=urn:btih:nested')
  })

  it('disables 下一步 until both title_key and link_key are selected', async () => {
    const wrapper = mountView()
    await flushPromises()
    await probeToStep2(wrapper)

    const nextBtn = wrapper.findAll('button').find((b) => b.text().includes('下一步'))
    expect(nextBtn).toBeDefined()
    expect(nextBtn!.attributes('disabled')).toBeDefined()

    const selects = wrapper.findAll('select.el-select')
    await selects[0].setValue('title')
    await selects[1].setValue('link')
    await wrapper.vm.$nextTick()

    expect(nextBtn!.attributes('disabled')).toBeUndefined()
  })

  it('shows the 唯一識別碼 column with resolved values when guid_key is selected', async () => {
    const wrapper = mountView()
    await flushPromises()
    await probeToStep2(wrapper)

    const selects = wrapper.findAll('select.el-select')
    await selects[0].setValue('title')
    await selects[1].setValue('link')
    await selects[2].setValue('guid')
    await wrapper.vm.$nextTick()

    const preview = wrapper.find('.ag-preview-list')
    expect(preview.text()).toContain('唯一識別碼')
    expect(preview.text()).toContain('guid-1')
  })

  it('does not render the 唯一識別碼 column when guid_key is empty', async () => {
    const wrapper = mountView()
    await flushPromises()
    await probeToStep2(wrapper)

    const selects = wrapper.findAll('select.el-select')
    await selects[0].setValue('title')
    await selects[1].setValue('link')
    await wrapper.vm.$nextTick()

    const preview = wrapper.find('.ag-preview-list')
    expect(preview.text()).not.toContain('唯一識別碼')
  })

  it('shows the 作者 column with resolved values when author_key is selected', async () => {
    mockProbeFeed.mockResolvedValue(
      makeProbeResult({
        available_keys: ['title', 'link', 'guid', 'author'],
        sample_entries: [
          { title: 'Example 01', link: 'https://example.com/1', guid: 'guid-1', author: 'Alice' },
        ],
      }),
    )
    const wrapper = mountView()
    await flushPromises()
    await probeToStep2(wrapper)

    const selects = wrapper.findAll('select.el-select')
    await selects[0].setValue('title')
    await selects[1].setValue('link')
    await selects[3].setValue('author')
    await wrapper.vm.$nextTick()

    const preview = wrapper.find('.ag-preview-list')
    expect(preview.text()).toContain('作者')
    expect(preview.text()).toContain('Alice')
  })

  it('does not render the 作者 column when author_key is empty', async () => {
    const wrapper = mountView()
    await flushPromises()
    await probeToStep2(wrapper)

    const selects = wrapper.findAll('select.el-select')
    await selects[0].setValue('title')
    await selects[1].setValue('link')
    await wrapper.vm.$nextTick()

    const preview = wrapper.find('.ag-preview-list')
    expect(preview.text()).not.toContain('作者')
  })

  it('carries an ag-wide-tooltip popperClass via show-overflow-tooltip on every long-content preview column', async () => {
    mockProbeFeed.mockResolvedValue(
      makeProbeResult({
        available_keys: ['title', 'link', 'guid', 'author'],
        sample_entries: [
          { title: 'Example 01', link: 'https://example.com/1', guid: 'guid-1', author: 'Alice' },
        ],
      }),
    )
    const wrapper = mountView()
    await flushPromises()
    await probeToStep2(wrapper)

    const selects = wrapper.findAll('select.el-select')
    await selects[0].setValue('title')
    await selects[1].setValue('link')
    await selects[2].setValue('guid')
    await selects[3].setValue('author')
    await wrapper.vm.$nextTick()

    const targetLabels = ['標題', '連結', '唯一識別碼', '作者']
    const allColumns = wrapper.findAllComponents(stubs.ElTableColumn) as unknown as PreviewColumnWrapper[]
    const previewColumns = allColumns.filter((column) => targetLabels.includes(column.props('label')))

    expect(new Set(previewColumns.map((column) => column.props('label')))).toEqual(
      new Set(targetLabels),
    )
    for (const column of previewColumns) {
      expect(column.vm.$attrs['show-overflow-tooltip']).toEqual({ popperClass: 'ag-wide-tooltip' })
    }
  })

  it('marks long-value columns with show-overflow-tooltip instead of wrapping/growing the panel', async () => {
    const longLink = `magnet:?xt=urn:btih:${'a'.repeat(800)}`
    mockProbeFeed.mockResolvedValue(
      makeProbeResult({
        sample_entries: [{ title: 'Example 01', link: longLink, guid: 'guid-1' }],
      }),
    )
    const wrapper = mountView()
    await flushPromises()
    await probeToStep2(wrapper)

    const selects = wrapper.findAll('select.el-select')
    await selects[0].setValue('title')
    await selects[1].setValue('link')
    await wrapper.vm.$nextTick()

    const linkCell = wrapper.find('td[data-label="連結"]')
    expect(linkCell.exists()).toBe(true)
    expect(linkCell.attributes('show-overflow-tooltip')).toBeDefined()
    expect(linkCell.text()).toBe(longLink)
  })

  it('renders one preview row per sample entry, capped at 5', async () => {
    mockProbeFeed.mockResolvedValue(
      makeProbeResult({
        sample_entries: Array.from({ length: 7 }, (_, i) => ({
          title: `Entry ${i}`,
          link: `https://example.com/${i}`,
          guid: `guid-${i}`,
        })),
      }),
    )
    const wrapper = mountView()
    await flushPromises()
    await probeToStep2(wrapper)

    const selects = wrapper.findAll('select.el-select')
    await selects[0].setValue('title')
    await selects[1].setValue('link')
    await wrapper.vm.$nextTick()

    const rows = wrapper.findAll('.ag-preview-table .el-table-row')
    expect(rows).toHaveLength(5)
  })
})

describe('FeedsTab — step 3: name + save', () => {
  async function probeAndPickFields(wrapper: ReturnType<typeof mountView>): Promise<void> {
    const urlInput = wrapper.find('input.el-input')
    await urlInput.setValue('https://share.dmhy.org/topics/rss/sort_id/2/rss.xml')
    const testBtn = wrapper.findAll('button').find((b) => b.text().includes('測試'))
    await testBtn!.trigger('click')
    await flushPromises()

    const selects = wrapper.findAll('select.el-select')
    await selects[0].setValue('title')
    await selects[1].setValue('link')
    await selects[2].setValue('guid')
    await wrapper.vm.$nextTick()

    const nextBtn = wrapper.findAll('button').find((b) => b.text().includes('下一步'))
    await nextBtn!.trigger('click')
    await wrapper.vm.$nextTick()
  }

  it('calls createFeed with the mapped payload and refreshes the list', async () => {
    const wrapper = mountView()
    await flushPromises()
    await probeAndPickFields(wrapper)

    const vm = wrapper.vm as unknown as FeedsTabVm
    expect(vm.step).toBe(3)

    const nameInputs = wrapper.findAll('input.el-input')
    const nameInput = nameInputs[nameInputs.length - 1]
    await nameInput.setValue('dmhy 動畫')

    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('儲存'))
    expect(saveBtn).toBeDefined()
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(mockCreateFeed).toHaveBeenCalledWith({
      name: 'dmhy 動畫',
      url: 'https://share.dmhy.org/topics/rss/sort_id/2/rss.xml',
      title_key: 'title',
      link_key: 'link',
      guid_key: 'guid',
      author_key: null,
      enabled: true,
    })
    expect(mockElMessageSuccess).toHaveBeenCalledWith('RSS 來源已儲存')
    expect(mockListFeeds).toHaveBeenCalledTimes(2) // initial mount + post-save reload
  })
})

describe('FeedsTab — feed table', () => {
  it('renders the mapping summary column', async () => {
    mockListFeeds.mockResolvedValue([makeFeed({ title_key: 'title', link_key: 'link', guid_key: 'guid' })])
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('title=title, link=link, guid=guid')
  })

  it('toggling the enabled switch calls updateFeed', async () => {
    mockListFeeds.mockResolvedValue([makeFeed({ id: 7, enabled: true })])
    const wrapper = mountView()
    await flushPromises()

    const toggle = wrapper.find('.el-table-row input.el-switch')
    await toggle.setValue(false)
    await flushPromises()

    expect(mockUpdateFeed).toHaveBeenCalledWith(7, { enabled: false })
  })

  it('delete button confirms then calls deleteFeed', async () => {
    mockListFeeds.mockResolvedValue([makeFeed({ id: 9, name: 'ToDelete' })])
    const wrapper = mountView()
    await flushPromises()

    const deleteBtn = wrapper.findAll('button').find((b) => b.text().trim() === '刪除')
    expect(deleteBtn).toBeDefined()
    await deleteBtn!.trigger('click')
    await flushPromises()

    expect(mockElMessageBoxConfirm).toHaveBeenCalledTimes(1)
    expect(mockDeleteFeed).toHaveBeenCalledWith(9)
  })

  it('does not delete when confirmation is cancelled', async () => {
    mockElMessageBoxConfirm.mockRejectedValue('cancel')
    mockListFeeds.mockResolvedValue([makeFeed({ id: 9 })])
    const wrapper = mountView()
    await flushPromises()

    const deleteBtn = wrapper.findAll('button').find((b) => b.text().trim() === '刪除')
    await deleteBtn!.trigger('click')
    await flushPromises()

    expect(mockDeleteFeed).not.toHaveBeenCalled()
  })

  it('renders entry_count column for each feed row', async () => {
    mockListFeeds.mockResolvedValue([
      makeFeed({ id: 1, name: 'dmhy 動畫', entry_count: 12 }),
      makeFeed({ id: 2, name: 'LoliHouse', entry_count: 0 }),
    ])
    const wrapper = mountView()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('12')

    const rows = wrapper.findAll('.el-table-row')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain('12')
    expect(rows[1].text()).toContain('0')
  })

  it('editing a row jumps directly to step 2 and probes automatically', async () => {
    mockListFeeds.mockResolvedValue([
      makeFeed({ id: 3, url: 'https://example.com/rss.xml', title_key: 'title', link_key: 'link' }),
    ])
    const wrapper = mountView()
    await flushPromises()

    const editBtn = wrapper.findAll('button').find((b) => b.text().trim() === '編輯')
    expect(editBtn).toBeDefined()
    await editBtn!.trigger('click')
    await flushPromises()

    expect(mockProbeFeed).toHaveBeenCalledWith('https://example.com/rss.xml')
    const vm = wrapper.vm as unknown as FeedsTabVm
    expect(vm.step).toBe(2)
    expect(vm.wizardMode).toBe('edit')
  })
})
