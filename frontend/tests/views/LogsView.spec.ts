import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ref } from 'vue'
import type { Ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import type { LogEntry } from '@/api/logs'
import { createElementPlusStubs } from '../helpers/elementPlusStubs'

// ---------------------------------------------------------------------------
// Stub LogStreamSocket (both paths) so the panel doesn't open a real WS
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

// Stub the inner LogStreamPanel so we control its output without touching
// the real component internals. This way LogsView tests remain focused.
vi.mock('@/components/LogStreamPanel.vue', () => ({
  default: {
    props: ['alwaysExpanded'],
    template: '<div class="log-stream-panel-stub" :data-always-expanded="alwaysExpanded" />',
  },
}))

import LogsView from '@/views/LogsView.vue'

const stubs = createElementPlusStubs()

function resetStubs() {
  stubState = ref<string>('open')
  stubLines = ref<LogEntry[]>([])
  mockConnect.mockReset()
  mockClose.mockReset()
}

function mountView() {
  return mount(LogsView, { global: { stubs } })
}

// ---------------------------------------------------------------------------
// Basic rendering
// ---------------------------------------------------------------------------
describe('LogsView — basic rendering', () => {
  beforeEach(resetStubs)

  it('renders the 系統日誌 page title', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('系統日誌')
  })

  it('renders LogStreamPanel', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.log-stream-panel-stub').exists()).toBe(true)
  })

  it('passes always-expanded=true to LogStreamPanel', async () => {
    const wrapper = mountView()
    await flushPromises()
    const panel = wrapper.find('.log-stream-panel-stub')
    expect(panel.attributes('data-always-expanded')).toBe('true')
  })
})
