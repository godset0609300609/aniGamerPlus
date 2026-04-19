import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ref } from 'vue'
import type { Ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { LogEntry } from '@/api/logs'
import { createElementPlusStubs } from '../helpers/elementPlusStubs'

// ---------------------------------------------------------------------------
// Stub LogStreamSocket so tests control state / lines directly
// ---------------------------------------------------------------------------
let stubState: Ref<string>
let stubLines: Ref<LogEntry[]>
const mockConnect = vi.fn()
const mockClose = vi.fn()

function buildMockSocket() {
  return {
    state: stubState,
    lines: stubLines,
    connect: mockConnect,
    close: mockClose,
  }
}

vi.mock('@/api/logs', () => ({
  LogStreamSocket: vi.fn().mockImplementation(() => buildMockSocket()),
}))

import LogStreamPanel from '@/components/LogStreamPanel.vue'

const stubs = createElementPlusStubs()

function makeEntry(overrides: Partial<LogEntry> = {}): LogEntry {
  return {
    timestamp: '2024-01-01T12:34:56Z',
    level: 'INFO',
    name: 'test',
    message: 'hello log',
    sn: null,
    ...overrides,
  }
}

function resetStubs() {
  stubState = ref<string>('open')
  stubLines = ref<LogEntry[]>([])
  mockConnect.mockReset()
  mockClose.mockReset()
}

function mountExpanded() {
  return mount(LogStreamPanel, {
    props: { alwaysExpanded: true },
    global: { stubs },
  })
}

// ---------------------------------------------------------------------------
// Layout structure — alwaysExpanded mode
// ---------------------------------------------------------------------------
describe('LogStreamPanel — layout (alwaysExpanded)', () => {
  beforeEach(resetStubs)

  it('test_filter_is_outside_scrollbar: filter bar is NOT a descendant of the log body', async () => {
    const wrapper = mountExpanded()
    await flushPromises()

    // The scrollable body has class log-stream-panel__body.
    // The filter bar has class log-stream-panel__filter.
    // They must be siblings, not parent/child.
    const body = wrapper.find('.log-stream-panel__body')
    expect(body.exists()).toBe(true)

    const filterInsideBody = body.find('.log-stream-panel__filter')
    expect(filterInsideBody.exists()).toBe(false)
  })

  it('test_body_has_scrollbar: the log body uses an el-scrollbar', async () => {
    stubLines.value = [makeEntry({ message: 'scroll-test' })]
    const wrapper = mountExpanded()
    await flushPromises()

    // The body element itself should be the el-scrollbar (or contain it).
    // Our stub renders ElScrollbar as <div class="el-scrollbar">.
    const scrollbar = wrapper.find('.log-stream-panel__body.el-scrollbar')
    expect(scrollbar.exists()).toBe(true)
    expect(scrollbar.text()).toContain('scroll-test')
  })

  it('filter bar contains the level select and keyword input', async () => {
    const wrapper = mountExpanded()
    await flushPromises()

    const filter = wrapper.find('.log-stream-panel__filter')
    expect(filter.find('.el-select').exists()).toBe(true)
    expect(filter.find('.el-input').exists()).toBe(true)
  })

  it('filter and body are both direct children of the expanded panel', async () => {
    const wrapper = mountExpanded()
    await flushPromises()

    const panel = wrapper.find('.log-stream-panel--expanded')
    expect(panel.exists()).toBe(true)

    // Both structural elements are direct children.
    const children = panel.element.children
    const classNames = Array.from(children).map((el) => el.className)
    expect(classNames.some((c) => c.includes('log-stream-panel__filter'))).toBe(true)
    expect(classNames.some((c) => c.includes('log-stream-panel__body'))).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Lifecycle — socket cleanup on unmount
// ---------------------------------------------------------------------------
describe('LogStreamPanel — socket lifecycle', () => {
  beforeEach(resetStubs)

  it('test_unmounting_panel_closes_socket: close() is called when the component unmounts', async () => {
    const wrapper = mountExpanded()
    await flushPromises()

    mockClose.mockClear()

    wrapper.unmount()

    expect(mockClose).toHaveBeenCalledTimes(1)
  })
})
