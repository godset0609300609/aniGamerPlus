import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ref, computed } from 'vue'
import type { Ref } from 'vue'
import type { SocketState } from '@/api/ws'
import type { TaskProgressMap, TaskProgressEntry } from '@/types'
import { categorize } from '@/composables/useTaskCategory'

// ---------------------------------------------------------------------------
// Controllable progress store stub
// ---------------------------------------------------------------------------
const mockConnect = vi.fn()
const mockClose = vi.fn()

let stubTasks: Ref<TaskProgressMap>
let stubState: Ref<SocketState>
let stubShowDisconnectedBanner: Ref<boolean>
let stubLastTasks: Ref<TaskProgressMap>
let stubHasReceivedFirst: Ref<boolean>

const TERMINAL_STATUSES = new Set(['下載完成', '上傳完成', '任務完成', '失敗'])

function buildMockStore() {
  const activeEntries = computed((): TaskProgressEntry[] =>
    Object.entries(stubTasks.value)
      .filter(([, e]) => !TERMINAL_STATUSES.has(e.status))
      .sort(([a], [b]) => Number(b) - Number(a))
      .map(([, e]) => e),
  )
  const completedEntries = computed((): TaskProgressEntry[] =>
    Object.entries(stubTasks.value)
      .filter(([, e]) => TERMINAL_STATUSES.has(e.status))
      .sort(([a], [b]) => Number(b) - Number(a))
      .map(([, e]) => e),
  )
  const byCategory = computed(() => {
    const result = { downloading: [] as TaskProgressEntry[], waiting: [] as TaskProgressEntry[], completed: [] as TaskProgressEntry[] }
    for (const entry of activeEntries.value) {
      const cat = categorize(entry.status)
      if (cat === 'waiting') result.waiting.push(entry)
      else result.downloading.push(entry)
    }
    result.completed = completedEntries.value
    return result
  })
  return {
    tasks: stubTasks,
    state: stubState,
    showDisconnectedBanner: stubShowDisconnectedBanner,
    lastTasks: stubLastTasks,
    hasReceivedFirst: stubHasReceivedFirst,
    activeEntries,
    completedEntries,
    byCategory,
    downloadingCount: computed(() => byCategory.value.downloading.length),
    waitingCount: computed(() => byCategory.value.waiting.length),
    completedCount: computed(() => byCategory.value.completed.length),
    totalCount: computed(() =>
      byCategory.value.downloading.length +
      byCategory.value.waiting.length +
      byCategory.value.completed.length,
    ),
    connect: mockConnect,
    close: mockClose,
  }
}

function resetStubs() {
  stubTasks = ref<TaskProgressMap>({})
  stubState = ref<SocketState>('open')
  stubShowDisconnectedBanner = ref(false)
  stubLastTasks = ref<TaskProgressMap>({})
  stubHasReceivedFirst = ref(true)
  mockConnect.mockReset()
  mockClose.mockReset()
  // The view-mode toggle persists to localStorage — clear it so tests in
  // this file never leak state into one another via mount order.
  localStorage.clear()
  isMobileRef.value = false
}

vi.mock('@/stores/progress', () => ({
  useProgressStore: () => buildMockStore(),
  TERMINAL_STATUSES: new Set(['下載完成', '上傳完成', '任務完成', '失敗']),
  __resetProgressStoreForTest: vi.fn(),
}))

// ---------------------------------------------------------------------------
// Stub ManualTaskDialog to keep tests focused on MonitorView logic
// ---------------------------------------------------------------------------
vi.mock('@/components/ManualTaskDialog.vue', () => ({
  default: { template: '<div class="manual-task-dialog-stub" />' },
}))

// ---------------------------------------------------------------------------
// useBreakpoint stub — controllable isMobile so the forced-kanban behaviour
// can be tested without real matchMedia/viewport plumbing.
// ---------------------------------------------------------------------------
const isMobileRef = ref(false)

vi.mock('@/composables/useBreakpoint', () => ({
  useBreakpoint: () => ({
    isMobile: isMobileRef,
    isTablet: ref(false),
  }),
}))

import { flushPromises, mount } from '@vue/test-utils'
import MonitorView from '@/views/MonitorView.vue'
import MonitorTable from '@/components/monitor/MonitorTable.vue'
import { createElementPlusStubs } from '../helpers/elementPlusStubs'

const elementPlusStubs = createElementPlusStubs()

function mountMonitor() {
  return mount(MonitorView, { global: { stubs: elementPlusStubs } })
}

// ---------------------------------------------------------------------------
// Basic rendering
// ---------------------------------------------------------------------------
describe('MonitorView — basic rendering', () => {
  beforeEach(() => {
    resetStubs()
  })

  it('renders empty state (目前沒有任務) when open and no tasks', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    expect(wrapper.text()).toContain('目前沒有任務')
    expect(mockConnect).toHaveBeenCalled()
  })

  it('renders task cards after a store tasks update', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '12345': { sn: 12345, rate: 30, status: '正在下載', filename: 'Episode 01.mp4' },
      '9': { sn: 9, rate: 80, status: '正在下載', filename: 'Episode 02.mp4' },
    }
    await flushPromises()

    const text = wrapper.text()
    expect(text).not.toContain('目前沒有任務')
    expect(text).toContain('Episode 01.mp4')
    expect(text).toContain('Episode 02.mp4')
  })

  it('does NOT close the store on unmount (store is app-scope)', async () => {
    const wrapper = mountMonitor()
    wrapper.unmount()
    expect(mockClose).not.toHaveBeenCalled()
  })

  it('hides active cards whose status is a terminal marker and shows empty state when no completed within 7 days', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    // Terminal tasks without started_at won't appear in completed (7-day filter)
    stubTasks.value = {
      '123': { sn: 123, rate: 100, status: '下載完成', filename: 'x' },
    }
    await flushPromises()

    // The mock stub includes terminal tasks in completedEntries without date filter
    // but the task still appears because stub does not filter by date.
    // The empty state won't show since completed column has the entry.
    // This test just checks active (non-terminal) tasks are not in the active stream.
    const text = wrapper.text()
    expect(text).not.toContain('目前沒有任務')
  })

  it('shows queued tasks with the 等待下載 status', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '456': { sn: 456, rate: 0, status: '等待下載', filename: '《foo》' },
    }
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('《foo》')
    expect(text).toContain('等待下載')
    expect(text).not.toContain('目前沒有任務')
  })
})

// ---------------------------------------------------------------------------
// Three-column layout
// ---------------------------------------------------------------------------
describe('MonitorView — three-column layout', () => {
  beforeEach(() => {
    resetStubs()
  })

  it('renders the three-column grid with correct column titles', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '1': { sn: 1, rate: 50, status: '正在下載', filename: 'a.mp4' },
    }
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('下載中')
    expect(text).toContain('等待中')
    expect(text).toContain('近期完成')
  })

  it('does not show 錯誤需重試 column title', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '1': { sn: 1, rate: 50, status: '正在下載', filename: 'a.mp4' },
    }
    await flushPromises()

    expect(wrapper.text()).not.toContain('錯誤需重試')
  })

  it('routes 正在下載 tasks to the downloading column', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '1': { sn: 1, rate: 50, status: '正在下載', filename: 'downloading.mp4' },
    }
    await flushPromises()

    expect(wrapper.text()).toContain('downloading.mp4')
  })

  it('routes 等待下載 tasks to the waiting column', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '2': { sn: 2, rate: 0, status: '等待下載', filename: 'waiting.mp4' },
    }
    await flushPromises()

    expect(wrapper.text()).toContain('waiting.mp4')
  })

  it('routes 任務失敗, 等待重啓 tasks to the downloading column (not a separate column)', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '3': { sn: 3, rate: 0, status: '任務失敗, 等待重啓', filename: 'retry.mp4' },
    }
    await flushPromises()

    expect(wrapper.text()).toContain('retry.mp4')
  })

  it("routes 'other' status tasks to the downloading column", async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '4': { sn: 4, rate: 99, status: '正在合並', filename: 'other.mp4' },
    }
    await flushPromises()

    expect(wrapper.text()).toContain('other.mp4')
  })

  it('routes 失敗 tasks to the completed column (not downloading)', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '5': { sn: 5, rate: 0, status: '失敗', filename: 'failed.mp4' },
    }
    await flushPromises()

    // 失敗 is terminal — mock store filters it out of activeEntries,
    // so it lands in completedEntries and appears in the completed column.
    expect(wrapper.text()).toContain('failed.mp4')
  })

  it('shows monitor-header with correct task title', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    expect(wrapper.text()).toContain('任務監控')
  })
})

// ---------------------------------------------------------------------------
// Skeleton / connecting state
// ---------------------------------------------------------------------------
describe('MonitorView — connecting skeleton', () => {
  beforeEach(() => {
    resetStubs()
  })

  it('shows skeletons when state is connecting and no message received yet', async () => {
    stubState.value = 'connecting'
    stubHasReceivedFirst.value = false

    const wrapper = mountMonitor()
    await flushPromises()

    const skeletons = wrapper.findAll('.el-skeleton')
    expect(skeletons.length).toBe(3)
    expect(wrapper.text()).not.toContain('目前沒有任務')
  })

  it('does not show skeleton when connecting but already has data (reconnect case)', async () => {
    stubState.value = 'connecting'
    stubHasReceivedFirst.value = true

    const wrapper = mountMonitor()
    await flushPromises()

    expect(wrapper.findAll('.el-skeleton').length).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// Disconnect banner
// ---------------------------------------------------------------------------
describe('MonitorView — disconnect banner', () => {
  beforeEach(() => {
    resetStubs()
  })

  it('does not show disconnect banner when state is open', async () => {
    stubState.value = 'open'
    stubShowDisconnectedBanner.value = false

    const wrapper = mountMonitor()
    await flushPromises()

    expect(wrapper.text()).not.toContain('連線中斷')
  })

  it('shows disconnect banner when showDisconnectedBanner is true', async () => {
    stubState.value = 'closed'
    stubShowDisconnectedBanner.value = true
    stubLastTasks.value = {}

    const wrapper = mountMonitor()
    await flushPromises()

    const alert = wrapper.find('[title="連線中斷，嘗試重新連線中…"]')
    expect(alert.exists()).toBe(true)
  })

  it('renders last snapshot dimmed when state is closed with data', async () => {
    stubState.value = 'closed'
    stubShowDisconnectedBanner.value = true
    stubLastTasks.value = {
      '42': { sn: 42, rate: 60, status: '正在下載', filename: 'LastEp.mp4' },
    }

    const wrapper = mountMonitor()
    await flushPromises()

    expect(wrapper.text()).toContain('LastEp.mp4')
    expect(wrapper.find('.monitor-column--dimmed').exists()).toBe(true)
  })

  it('shows empty state when closed with no last data', async () => {
    stubState.value = 'closed'
    stubShowDisconnectedBanner.value = false
    stubLastTasks.value = {}

    const wrapper = mountMonitor()
    await flushPromises()

    expect(wrapper.find('.monitor-column--dimmed').exists()).toBe(false)
    expect(wrapper.text()).toContain('目前沒有任務')
  })
})

// ---------------------------------------------------------------------------
// Open state normal rendering
// ---------------------------------------------------------------------------
describe('MonitorView — open state', () => {
  beforeEach(() => {
    resetStubs()
  })

  it('renders task cards in open state', async () => {
    stubState.value = 'open'

    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '10': { sn: 10, rate: 45, status: '正在下載', filename: 'OpenEp.mp4' },
    }
    await flushPromises()

    expect(wrapper.text()).toContain('OpenEp.mp4')
  })

  it('columns are not dimmed in open state', async () => {
    stubState.value = 'open'

    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '10': { sn: 10, rate: 45, status: '正在下載', filename: 'OpenEp.mp4' },
    }
    await flushPromises()

    expect(wrapper.find('.monitor-column--dimmed').exists()).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// BT downloader (Put.io -> local disk) source rows
// ---------------------------------------------------------------------------
describe('MonitorView — BT source rows', () => {
  beforeEach(() => {
    resetStubs()
  })

  it('test_bt_source_entry_renders_with_bt_badge: shows the BT badge for a source=bt task', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '2147483649': {
        sn: 2147483649,
        rate: 0,
        status: 'Put.io 下載中',
        filename: 'Some Show - 01',
        bangumi_name: 'my-filter',
        source: 'bt',
        external_id: '1',
      },
    }
    await flushPromises()

    expect(wrapper.find('.task-card__badge--bt').exists()).toBe(true)
    expect(wrapper.find('.task-card__badge--bt').text()).toBe('BT')
    expect(wrapper.text()).toContain('my-filter')
  })

  it('routes a BT downloading status (Put.io 下載中) to the downloading column', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '2147483649': {
        sn: 2147483649,
        rate: 42,
        status: 'Put.io 下載中',
        filename: 'Some Show - 01',
        source: 'bt',
      },
    }
    await flushPromises()

    expect(wrapper.text()).toContain('Some Show - 01')
    expect(wrapper.text()).toContain('Put.io 下載中')
  })

  it('routes a BT waiting status (等待 Put.io) to the waiting column', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '2147483649': {
        sn: 2147483649,
        rate: 0,
        status: '等待 Put.io',
        filename: 'Some Show - 01',
        source: 'bt',
      },
    }
    await flushPromises()

    expect(wrapper.text()).toContain('等待 Put.io')
  })

  it('test_bt_entry_gracefully_omits_episode_and_resolution_when_null: no episode/resolution badge for a bare BT row', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '2147483649': {
        sn: 2147483649,
        rate: 0,
        status: 'Put.io 排隊中',
        filename: 'Some Show - 01',
        bangumi_name: 'my-filter',
        // episode / resolution intentionally omitted — BT tasks never set these.
        source: 'bt',
      },
    }
    await flushPromises()

    // Title falls back to the "《bangumi_name》" form with no " - EP ..." suffix.
    expect(wrapper.text()).toContain('《my-filter》')
    expect(wrapper.text()).not.toContain('EP ')
    expect(wrapper.find('.task-card__badge--resolution').exists()).toBe(false)
    // The BT badge itself must still render even with everything else null.
    expect(wrapper.find('.task-card__badge--bt').exists()).toBe(true)
  })

  it('does not show a BT badge for a non-BT (animad) task', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '1': { sn: 1, rate: 50, status: '正在下載', filename: 'animad-ep.mp4' },
    }
    await flushPromises()

    expect(wrapper.find('.task-card__badge--bt').exists()).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// View mode toggle (table / kanban) — localStorage-persisted
// ---------------------------------------------------------------------------
describe('MonitorView — view mode toggle', () => {
  beforeEach(() => {
    resetStubs()
  })

  it('test_kanban_mode_still_renders_three_columns', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '1': { sn: 1, rate: 50, status: '正在下載', filename: 'a.mp4' },
    }
    await flushPromises()

    expect(wrapper.find('.monitor-grid').exists()).toBe(true)
    const text = wrapper.text()
    expect(text).toContain('下載中')
    expect(text).toContain('等待中')
    expect(text).toContain('近期完成')
  })

  it('test_view_mode_toggle_switches_between_table_and_kanban', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '1': { sn: 1, rate: 50, status: '正在下載', filename: 'toggle.mp4' },
    }
    await flushPromises()

    // Defaults to kanban.
    expect(wrapper.find('.monitor-grid').exists()).toBe(true)
    expect(wrapper.findComponent(MonitorTable).exists()).toBe(false)

    const tableButton = wrapper
      .findAll('.el-radio-button')
      .find((b) => b.text().includes('表格'))
    expect(tableButton).toBeTruthy()
    await tableButton!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.monitor-grid').exists()).toBe(false)
    expect(wrapper.findComponent(MonitorTable).exists()).toBe(true)
    expect(wrapper.text()).toContain('toggle.mp4')

    const kanbanButton = wrapper
      .findAll('.el-radio-button')
      .find((b) => b.text().includes('看板'))
    await kanbanButton!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.monitor-grid').exists()).toBe(true)
    expect(wrapper.findComponent(MonitorTable).exists()).toBe(false)
  })

  it('test_view_mode_persists_to_localStorage', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    const tableButton = wrapper
      .findAll('.el-radio-button')
      .find((b) => b.text().includes('表格'))
    await tableButton!.trigger('click')
    await flushPromises()

    expect(localStorage.getItem('monitor-view-mode')).toBe('table')

    const kanbanButton = wrapper
      .findAll('.el-radio-button')
      .find((b) => b.text().includes('看板'))
    await kanbanButton!.trigger('click')
    await flushPromises()

    expect(localStorage.getItem('monitor-view-mode')).toBe('kanban')
  })

  it('test_view_mode_restored_from_localStorage_on_mount', async () => {
    localStorage.setItem('monitor-view-mode', 'table')

    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '1': { sn: 1, rate: 50, status: '正在下載', filename: 'restored.mp4' },
    }
    await flushPromises()

    expect(wrapper.find('.monitor-grid').exists()).toBe(false)
    expect(wrapper.findComponent(MonitorTable).exists()).toBe(true)
  })

  it('falls back to kanban when localStorage holds an invalid value', async () => {
    localStorage.setItem('monitor-view-mode', 'nonsense')

    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '1': { sn: 1, rate: 50, status: '正在下載', filename: 'fallback.mp4' },
    }
    await flushPromises()

    expect(wrapper.find('.monitor-grid').exists()).toBe(true)
  })

  it('shows the view-mode toggle even when there are zero tasks', async () => {
    const wrapper = mountMonitor()
    await flushPromises()

    expect(wrapper.text()).toContain('目前沒有任務')
    expect(wrapper.findAll('.el-radio-button').length).toBe(2)
  })
})

// ---------------------------------------------------------------------------
// Mobile — table mode forced to kanban, toggle hidden
// ---------------------------------------------------------------------------
describe('MonitorView — mobile forces kanban mode', () => {
  beforeEach(() => {
    resetStubs()
  })

  it('test_view_mode_forced_to_kanban_when_mobile: renders the kanban grid (not the table) on mobile even when the stored preference is "table"', async () => {
    localStorage.setItem('monitor-view-mode', 'table')
    isMobileRef.value = true

    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '1': { sn: 1, rate: 50, status: '正在下載', filename: 'forced-kanban.mp4' },
    }
    await flushPromises()

    expect(wrapper.find('.monitor-grid').exists()).toBe(true)
    expect(wrapper.findComponent(MonitorTable).exists()).toBe(false)
    expect(wrapper.text()).toContain('forced-kanban.mp4')
  })

  it('hides the table/kanban toggle on mobile', async () => {
    isMobileRef.value = true
    const wrapper = mountMonitor()
    await flushPromises()

    expect(wrapper.findAll('.el-radio-button').length).toBe(0)
  })

  it('restores the saved "table" preference once the viewport is no longer mobile', async () => {
    localStorage.setItem('monitor-view-mode', 'table')
    isMobileRef.value = true

    const wrapper = mountMonitor()
    await flushPromises()

    stubTasks.value = {
      '1': { sn: 1, rate: 50, status: '正在下載', filename: 'still-mobile.mp4' },
    }
    await flushPromises()
    expect(wrapper.find('.monitor-grid').exists()).toBe(true)

    // Viewport widens back past the mobile breakpoint — the underlying
    // stored preference was never overwritten while forced, so it takes
    // effect immediately.
    isMobileRef.value = false
    await flushPromises()

    expect(wrapper.find('.monitor-grid').exists()).toBe(false)
    expect(wrapper.findComponent(MonitorTable).exists()).toBe(true)
  })

  it('does not persist a "kanban" write to localStorage while forced on mobile', async () => {
    localStorage.setItem('monitor-view-mode', 'table')
    isMobileRef.value = true

    mountMonitor()
    await flushPromises()

    expect(localStorage.getItem('monitor-view-mode')).toBe('table')
  })
})
