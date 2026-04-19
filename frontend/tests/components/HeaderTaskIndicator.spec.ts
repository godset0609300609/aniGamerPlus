import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ref, computed } from 'vue'
import type { TaskProgressEntry } from '@/types'

// ---------------------------------------------------------------------------
// Mock the progress store
// ---------------------------------------------------------------------------
const mockTotalCount = ref(0)
const mockDownloadingCount = ref(0)
const mockRetryCount = ref(0)
const mockWaitingCount = ref(0)
const mockActiveEntries = ref<TaskProgressEntry[]>([])

vi.mock('@/stores/progress', () => ({
  useProgressStore: () => ({
    totalCount: mockTotalCount,
    downloadingCount: mockDownloadingCount,
    retryCount: mockRetryCount,
    waitingCount: mockWaitingCount,
    activeEntries: mockActiveEntries,
    state: ref('open'),
    showDisconnectedBanner: ref(false),
    lastTasks: ref({}),
    hasReceivedFirst: ref(true),
    connect: vi.fn(),
    close: vi.fn(),
    byCategory: computed(() => ({ downloading: [], waiting: [], completed: [] })),
  }),
}))

// ---------------------------------------------------------------------------
// Mock vue-router
// ---------------------------------------------------------------------------
const mockPush = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mockPush }),
}))

// ---------------------------------------------------------------------------
// Import component AFTER mocks
// ---------------------------------------------------------------------------
import { mount } from '@vue/test-utils'
import HeaderTaskIndicator from '@/components/HeaderTaskIndicator.vue'
import { createElementPlusStubs } from '../helpers/elementPlusStubs'

const stubs = createElementPlusStubs()

function mountIndicator() {
  return mount(HeaderTaskIndicator, {
    global: {
      stubs,
    },
  })
}

function resetCounts() {
  mockTotalCount.value = 0
  mockDownloadingCount.value = 0
  mockRetryCount.value = 0
  mockWaitingCount.value = 0
  mockActiveEntries.value = []
  mockPush.mockReset()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe('HeaderTaskIndicator — hidden when no tasks', () => {
  beforeEach(resetCounts)

  it('does not render when totalCount is 0', () => {
    mockTotalCount.value = 0
    const wrapper = mountIndicator()
    // v-if="store.totalCount.value > 0" — el-popover should not be in DOM
    expect(wrapper.find('.el-popover').exists()).toBe(false)
  })

  it('does not render when only completed tasks exist (totalCount stays 0)', () => {
    // Simulate: 3 completed entries but no active (downloading/waiting) tasks.
    // totalCount now excludes completed, so it remains 0.
    mockTotalCount.value = 0
    mockDownloadingCount.value = 0
    mockWaitingCount.value = 0
    const wrapper = mountIndicator()
    expect(wrapper.find('.el-popover').exists()).toBe(false)
  })
})

describe('HeaderTaskIndicator — shown when tasks exist', () => {
  beforeEach(resetCounts)

  it('renders when totalCount > 0', () => {
    mockTotalCount.value = 2
    const wrapper = mountIndicator()
    expect(wrapper.find('.el-popover').exists()).toBe(true)
  })

  it('badge shows the totalCount value', () => {
    mockTotalCount.value = 3
    const wrapper = mountIndicator()
    const badge = wrapper.find('.el-badge')
    expect(badge.exists()).toBe(true)
    expect(badge.attributes('data-value')).toBe('3')
  })
})

describe('HeaderTaskIndicator — iconClass', () => {
  beforeEach(resetCounts)

  it('applies ag-indicator-downloading when downloadingCount > 0 and no retry', () => {
    mockTotalCount.value = 1
    mockDownloadingCount.value = 1
    mockRetryCount.value = 0
    const wrapper = mountIndicator()
    const btn = wrapper.find('button')
    expect(btn.classes()).toContain('ag-indicator-downloading')
  })

  it('applies ag-indicator-error when retryCount > 0 (takes priority)', () => {
    mockTotalCount.value = 2
    mockDownloadingCount.value = 1
    mockRetryCount.value = 1
    const wrapper = mountIndicator()
    const btn = wrapper.find('button')
    expect(btn.classes()).toContain('ag-indicator-error')
    expect(btn.classes()).not.toContain('ag-indicator-downloading')
  })

  it('applies ag-indicator-waiting when only waiting tasks exist', () => {
    mockTotalCount.value = 1
    mockDownloadingCount.value = 0
    mockRetryCount.value = 0
    mockWaitingCount.value = 1
    const wrapper = mountIndicator()
    const btn = wrapper.find('button')
    expect(btn.classes()).toContain('ag-indicator-waiting')
  })
})

describe('HeaderTaskIndicator — navigation', () => {
  beforeEach(resetCounts)

  it('calls router.push("/monitor") on button click', async () => {
    mockTotalCount.value = 1
    const wrapper = mountIndicator()
    await wrapper.find('button').trigger('click')
    expect(mockPush).toHaveBeenCalledWith('/monitor')
  })
})
