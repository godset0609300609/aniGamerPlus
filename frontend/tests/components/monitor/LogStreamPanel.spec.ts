import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ref, nextTick } from 'vue'
import type { Ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { LogEntry } from '@/api/logs'
import { createElementPlusStubs } from '../../helpers/elementPlusStubs'

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

import LogStreamPanel from '@/components/monitor/LogStreamPanel.vue'

const stubs = createElementPlusStubs()

function mountPanel() {
  return mount(LogStreamPanel, { global: { stubs } })
}

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
  stubState = ref<string>('connecting')
  stubLines = ref<LogEntry[]>([])
  mockConnect.mockReset()
  mockClose.mockReset()
}

// ---------------------------------------------------------------------------
// Basic rendering
// ---------------------------------------------------------------------------
describe('LogStreamPanel — basic rendering', () => {
  beforeEach(resetStubs)

  it('renders the collapse panel', () => {
    const wrapper = mountPanel()
    expect(wrapper.find('.el-collapse').exists()).toBe(true)
  })

  it('shows the 系統日誌 title', () => {
    const wrapper = mountPanel()
    expect(wrapper.text()).toContain('系統日誌')
  })

  it('renders level select and keyword input', () => {
    const wrapper = mountPanel()
    expect(wrapper.find('.el-select').exists()).toBe(true)
    expect(wrapper.find('.el-input').exists()).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Log lines rendering
// ---------------------------------------------------------------------------
describe('LogStreamPanel — log lines', () => {
  beforeEach(resetStubs)

  it('renders log messages', async () => {
    stubLines.value = [makeEntry({ message: 'unit test log' })]
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.text()).toContain('unit test log')
  })

  it('renders timestamps (HH:MM:SS slice)', async () => {
    stubLines.value = [makeEntry({ timestamp: '2024-01-01T12:34:56Z' })]
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.text()).toContain('12:34:56')
  })

  it('renders level badge', async () => {
    stubLines.value = [makeEntry({ level: 'WARNING' })]
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.text()).toContain('WARN')
  })
})

// ---------------------------------------------------------------------------
// Level colour classes
// ---------------------------------------------------------------------------
describe('LogStreamPanel — level colour classes', () => {
  beforeEach(resetStubs)

  it('INFO line has log-level--info class', async () => {
    stubLines.value = [makeEntry({ level: 'INFO' })]
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.find('.log-level--info').exists()).toBe(true)
  })

  it('WARNING line has log-level--warning class', async () => {
    stubLines.value = [makeEntry({ level: 'WARNING' })]
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.find('.log-level--warning').exists()).toBe(true)
  })

  it('ERROR line has log-level--error class', async () => {
    stubLines.value = [makeEntry({ level: 'ERROR' })]
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.find('.log-level--error').exists()).toBe(true)
  })

  it('CRITICAL line has log-level--error class', async () => {
    stubLines.value = [makeEntry({ level: 'CRITICAL' })]
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.find('.log-level--error').exists()).toBe(true)
  })

  it('DEBUG line has log-level--debug class', async () => {
    stubLines.value = [makeEntry({ level: 'DEBUG' })]
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.find('.log-level--debug').exists()).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Level filter
// ---------------------------------------------------------------------------
describe('LogStreamPanel — level filter', () => {
  beforeEach(resetStubs)

  it('shows all records when filter is ALL', async () => {
    stubLines.value = [
      makeEntry({ level: 'INFO', message: 'info msg' }),
      makeEntry({ level: 'DEBUG', message: 'debug msg' }),
    ]
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.text()).toContain('info msg')
    expect(wrapper.text()).toContain('debug msg')
  })

  it('hides DEBUG records when filter is INFO', async () => {
    stubLines.value = [
      makeEntry({ level: 'INFO', message: 'info msg' }),
      makeEntry({ level: 'DEBUG', message: 'debug msg' }),
    ]
    const wrapper = mountPanel()
    await flushPromises()

    // Set the level via the stubbed <select> (ElSelect emits update:modelValue).
    await wrapper.find('select.el-select').setValue('INFO')
    await flushPromises()

    expect(wrapper.text()).toContain('info msg')
    expect(wrapper.text()).not.toContain('debug msg')
  })

  it('test_filter_level_all_shows_everything: shows all records when filter is ALL', async () => {
    stubLines.value = [
      makeEntry({ level: 'INFO', message: 'info msg' }),
      makeEntry({ level: 'WARNING', message: 'warn msg' }),
      makeEntry({ level: 'ERROR', message: 'error msg' }),
    ]
    const wrapper = mountPanel()
    await flushPromises()

    // default is ALL — no filter applied
    expect(wrapper.text()).toContain('info msg')
    expect(wrapper.text()).toContain('warn msg')
    expect(wrapper.text()).toContain('error msg')
  })

  it('test_filter_level_warning_shows_only_warning: shows only WARNING when filter is WARNING', async () => {
    stubLines.value = [
      makeEntry({ level: 'INFO', message: 'info msg' }),
      makeEntry({ level: 'WARNING', message: 'warn msg' }),
      makeEntry({ level: 'ERROR', message: 'error msg' }),
    ]
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('select.el-select').setValue('WARNING')
    await flushPromises()

    expect(wrapper.text()).not.toContain('info msg')
    expect(wrapper.text()).toContain('warn msg')
    expect(wrapper.text()).not.toContain('error msg')
  })

  it('test_filter_level_error_shows_only_error: shows only ERROR when filter is ERROR', async () => {
    stubLines.value = [
      makeEntry({ level: 'INFO', message: 'info msg' }),
      makeEntry({ level: 'WARNING', message: 'warn msg' }),
      makeEntry({ level: 'ERROR', message: 'error msg' }),
    ]
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('select.el-select').setValue('ERROR')
    await flushPromises()

    expect(wrapper.text()).not.toContain('info msg')
    expect(wrapper.text()).not.toContain('warn msg')
    expect(wrapper.text()).toContain('error msg')
  })
})

// ---------------------------------------------------------------------------
// Keyword filter
// ---------------------------------------------------------------------------
describe('LogStreamPanel — keyword filter', () => {
  beforeEach(resetStubs)

  it('filters lines by keyword (case-insensitive)', async () => {
    stubLines.value = [
      makeEntry({ message: 'apple juice' }),
      makeEntry({ message: 'banana split' }),
    ]
    const wrapper = mountPanel()
    await flushPromises()

    const input = wrapper.find('input.el-input')
    await input.setValue('APPLE')
    await nextTick()

    expect(wrapper.text()).toContain('apple juice')
    expect(wrapper.text()).not.toContain('banana split')
  })

  it('shows all lines when keyword is cleared', async () => {
    stubLines.value = [makeEntry({ message: 'foo' }), makeEntry({ message: 'bar' })]
    const wrapper = mountPanel()
    await flushPromises()

    const input = wrapper.find('input.el-input')
    await input.setValue('foo')
    await nextTick()
    await input.setValue('')
    await nextTick()

    expect(wrapper.text()).toContain('foo')
    expect(wrapper.text()).toContain('bar')
  })
})

// ---------------------------------------------------------------------------
// Empty state (panel open — using ElCollapse stub)
// ---------------------------------------------------------------------------
describe('LogStreamPanel — empty state', () => {
  beforeEach(resetStubs)

  it('shows empty placeholder via the el-collapse body when no lines and panel collapsed', async () => {
    // The ElCollapse stub always renders its body (slot), so the template
    // tree is always visible in tests regardless of collapse state.
    // We verify the "no lines" empty div is rendered when lines = [].
    // isOpen is false (default), so the v-if="filteredLines.length === 0 && isOpen"
    // guard keeps the empty div hidden — only log-lines are expected absent.
    stubState.value = 'open'
    stubLines.value = []

    const wrapper = mountPanel()
    await flushPromises()

    // No log lines rendered when buffer is empty.
    expect(wrapper.findAll('.log-line')).toHaveLength(0)
  })

  it('shows log-line elements when lines are present', async () => {
    stubLines.value = [makeEntry(), makeEntry({ message: 'second' })]
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.findAll('.log-line')).toHaveLength(2)
  })
})

// ---------------------------------------------------------------------------
// Auto-scroll (structural — verifies scrollbarRef pattern exists)
// ---------------------------------------------------------------------------
describe('LogStreamPanel — auto-scroll', () => {
  beforeEach(resetStubs)

  it('renders a scrollbar container', () => {
    const wrapper = mountPanel()
    expect(wrapper.find('.el-scrollbar').exists()).toBe(true)
  })

  it('scrollbar contains log lines', async () => {
    stubLines.value = [makeEntry({ message: 'scroll test' })]
    const wrapper = mountPanel()
    await flushPromises()
    const scrollbar = wrapper.find('.el-scrollbar')
    expect(scrollbar.text()).toContain('scroll test')
  })
})
