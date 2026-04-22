import { describe, expect, it, vi, afterEach, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import TaskCard from '@/components/monitor/TaskCard.vue'
import type { TaskProgressEntry } from '@/types'
type CardVariant = 'downloading' | 'waiting' | 'completed' | 'retry' | 'other'
import { createElementPlusStubs } from '../../helpers/elementPlusStubs'

// Module-level mocks (hoisted by Vitest automatically).
vi.mock('element-plus', async (importOriginal) => {
  const mod = (await importOriginal()) as Record<string, unknown>
  return {
    ...mod,
    ElMessageBox: {
      confirm: vi.fn().mockResolvedValue('confirm'),
      alert: vi.fn(),
      prompt: vi.fn(),
    },
    ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() },
  }
})

vi.mock('@/api/client', async (importOriginal) => {
  const mod = (await importOriginal()) as Record<string, unknown>
  return { ...mod, cancelTask: vi.fn().mockResolvedValue(undefined) }
})

const stubs = createElementPlusStubs({
  ElProgress: {
    props: ['percentage', 'strokeWidth', 'showText', 'format', 'status'],
    template: '<div class="el-progress" :data-percentage="percentage" :data-status="status">{{ percentage }}%</div>',
  },
})

function makeTask(overrides: Partial<TaskProgressEntry> = {}): TaskProgressEntry {
  return {
    sn: 12345,
    rate: 50,
    status: '正在下載',
    filename: 'Episode 01.mp4',
    ...overrides,
  }
}

function mountCard(task: TaskProgressEntry, variant: CardVariant = 'downloading') {
  return mount(TaskCard, {
    props: { task, variant },
    global: { stubs },
  })
}

describe('TaskCard — title display', () => {
  it('shows bangumi_name and episode when available', () => {
    const wrapper = mountCard(
      makeTask({ bangumi_name: '鬼滅之刃', episode: '3', filename: 'foo.mp4' }),
    )
    const text = wrapper.text()
    expect(text).toContain('鬼滅之刃')
    expect(text).toContain('EP 3')
    expect(text).not.toContain('foo.mp4')
  })

  it('falls back to filename when bangumi_name is absent', () => {
    const wrapper = mountCard(makeTask({ bangumi_name: undefined, filename: 'fallback.mp4' }))
    expect(wrapper.text()).toContain('fallback.mp4')
  })

  it('shows bangumi_name without episode when episode is absent', () => {
    const wrapper = mountCard(makeTask({ bangumi_name: '進擊的巨人', episode: undefined }))
    const text = wrapper.text()
    expect(text).toContain('進擊的巨人')
    expect(text).not.toContain('EP')
  })
})

describe('TaskCard — badges', () => {
  it('shows resolution badge when resolution is set', () => {
    const wrapper = mountCard(makeTask({ resolution: '1080p' }))
    expect(wrapper.text()).toContain('1080p')
  })

  it('hides resolution badge when resolution is absent', () => {
    const wrapper = mountCard(makeTask({ resolution: undefined }))
    expect(wrapper.find('.task-card__badge--resolution').exists()).toBe(false)
  })

  it('shows retry badge when retries > 0', () => {
    const wrapper = mountCard(makeTask({ retries: 2 }))
    expect(wrapper.text()).toContain('重試 2')
  })

  it('hides retry badge when retries is 0', () => {
    const wrapper = mountCard(makeTask({ retries: 0 }))
    expect(wrapper.find('.task-card__badge--retry').exists()).toBe(false)
  })

  it('hides retry badge when retries is absent', () => {
    const wrapper = mountCard(makeTask({ retries: undefined }))
    expect(wrapper.find('.task-card__badge--retry').exists()).toBe(false)
  })
})

describe('TaskCard — ETA and speed', () => {
  it('shows ETA when eta_seconds is provided', () => {
    const wrapper = mountCard(makeTask({ eta_seconds: 80 }))
    expect(wrapper.text()).toContain('ETA 1m 20s')
  })

  it('hides ETA when eta_seconds is null', () => {
    const wrapper = mountCard(makeTask({ eta_seconds: null }))
    expect(wrapper.text()).not.toContain('ETA')
  })

  it('shows speed when speed_mbps is provided', () => {
    const wrapper = mountCard(makeTask({ speed_mbps: 3.2 }))
    expect(wrapper.text()).toContain('3.2 MB/s')
  })

  it('hides speed when speed_mbps is null', () => {
    const wrapper = mountCard(makeTask({ speed_mbps: null }))
    expect(wrapper.text()).not.toContain('MB/s')
  })

  it('speed and ETA appear in footer, not in status-row', () => {
    const wrapper = mountCard(makeTask({ speed_mbps: 5.5, eta_seconds: 60 }))
    const footer = wrapper.find('.task-card__footer')
    const statusRow = wrapper.find('.task-card__status-row')
    expect(footer.exists()).toBe(true)
    expect(footer.text()).toContain('5.5 MB/s')
    expect(footer.text()).toContain('ETA 1m 0s')
    expect(statusRow.text()).not.toContain('ETA')
    expect(statusRow.text()).not.toContain('MB/s')
  })
})

describe('TaskCard — relative time', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('shows relative time when started_at is provided', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-04-18T12:05:00Z'))
    const wrapper = mountCard(
      makeTask({ started_at: new Date('2026-04-18T12:00:00Z').toISOString() }),
    )
    expect(wrapper.text()).toContain('開始於')
  })

  it('hides relative time when started_at is null', () => {
    const wrapper = mountCard(makeTask({ started_at: null }))
    expect(wrapper.text()).not.toContain('開始於')
  })
})

describe('TaskCard — variants and border color', () => {
  it('applies success color for downloading variant', () => {
    const wrapper = mountCard(makeTask(), 'downloading')
    const card = wrapper.find('.task-card')
    expect(card.attributes('style')).toContain('var(--el-color-success)')
  })

  it('applies info color for waiting variant', () => {
    const wrapper = mountCard(makeTask(), 'waiting')
    const card = wrapper.find('.task-card')
    expect(card.attributes('style')).toContain('var(--el-color-info)')
  })

  it('applies danger color for retry variant', () => {
    const wrapper = mountCard(makeTask(), 'retry')
    const card = wrapper.find('.task-card')
    expect(card.attributes('style')).toContain('var(--el-color-danger)')
  })
})

describe('TaskCard — percentage display', () => {
  it('clamps percentage to 0-100', () => {
    const wrapper = mountCard(makeTask({ rate: 150 }))
    expect(wrapper.text()).toContain('100%')
  })

  it('rounds percentage', () => {
    const wrapper = mountCard(makeTask({ rate: 42.7 }))
    expect(wrapper.text()).toContain('43%')
  })

  it('renders el-progress element', () => {
    const wrapper = mountCard(makeTask({ rate: 60 }))
    expect(wrapper.find('.el-progress').exists()).toBe(true)
  })

  it('passes correct percentage to el-progress', () => {
    const wrapper = mountCard(makeTask({ rate: 75.4 }))
    const bar = wrapper.find('.el-progress')
    expect(bar.attributes('data-percentage')).toBe('75')
  })

  it('passes status="exception" to el-progress for retry variant', () => {
    const wrapper = mountCard(makeTask(), 'retry')
    const bar = wrapper.find('.el-progress')
    expect(bar.attributes('data-status')).toBe('exception')
  })

  it('does not pass exception status for downloading variant', () => {
    const wrapper = mountCard(makeTask(), 'downloading')
    const bar = wrapper.find('.el-progress')
    expect(bar.attributes('data-status')).toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// Cooldown countdown
// ---------------------------------------------------------------------------

describe('TaskCard — cooldown countdown', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('shows cooldown remaining text when cooldown_until is in the future', () => {
    vi.useFakeTimers()
    const now = new Date('2026-04-19T10:00:00Z')
    vi.setSystemTime(now)
    const futureTs = new Date(now.getTime() + 30_000).toISOString() // 30s ahead
    const wrapper = mountCard(makeTask({ cooldown_until: futureTs }))
    const text = wrapper.text()
    expect(text).toContain('冷卻')
    expect(text).toMatch(/\d+s/)
  })

  it('hides cooldown text when cooldown_until is in the past', () => {
    vi.useFakeTimers()
    const now = new Date('2026-04-19T10:00:00Z')
    vi.setSystemTime(now)
    const pastTs = new Date(now.getTime() - 10_000).toISOString() // 10s ago
    const wrapper = mountCard(makeTask({ cooldown_until: pastTs }))
    expect(wrapper.find('.task-card__cooldown').exists()).toBe(false)
  })

  it('hides cooldown text when cooldown_until is null', () => {
    const wrapper = mountCard(makeTask({ cooldown_until: null }))
    expect(wrapper.find('.task-card__cooldown').exists()).toBe(false)
  })

  it('hides cooldown text when cooldown_until is not provided', () => {
    const wrapper = mountCard(makeTask({}))
    expect(wrapper.find('.task-card__cooldown').exists()).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Task 2 — progress % text / status text spacing
// ---------------------------------------------------------------------------

describe('TaskCard — status row spacing', () => {
  it('task-card__status has margin-left class applied (CSS class present)', () => {
    const wrapper = mountCard(makeTask({ status: '正在解析' }))
    const statusSpan = wrapper.find('.task-card__status')
    expect(statusSpan.exists()).toBe(true)
    // The element uses the scoped CSS class; verify the element is present and
    // carries the expected class so the scoped margin-left rule fires.
    expect(statusSpan.classes()).toContain('task-card__status')
  })

  it('task-card__progress element exists and carries its spacing class', () => {
    const wrapper = mountCard(makeTask())
    const progress = wrapper.find('.task-card__progress')
    expect(progress.exists()).toBe(true)
    expect(progress.classes()).toContain('task-card__progress')
  })

  it('cooldown text and status text coexist in the same status row', () => {
    vi.useFakeTimers()
    const now = new Date('2026-04-19T10:00:00Z')
    vi.setSystemTime(now)
    const futureTs = new Date(now.getTime() + 42_000).toISOString()
    const wrapper = mountCard(makeTask({ status: '正在解析', cooldown_until: futureTs }))
    const row = wrapper.find('.task-card__status-row')
    expect(row.exists()).toBe(true)
    // Both status text and cooldown text should appear inside the same row.
    const text = row.text()
    expect(text).toContain('正在解析')
    expect(text).toContain('冷卻')
    vi.useRealTimers()
  })
})

// ---------------------------------------------------------------------------
// Batch H — cancel button
// ---------------------------------------------------------------------------

describe('TaskCard — cancel button', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders a cancel button with class cancel-btn for downloading variant', () => {
    const wrapper = mountCard(makeTask(), 'downloading')
    expect(wrapper.find('.cancel-btn').exists()).toBe(true)
  })

  it('hides cancel button when variant is completed', () => {
    const wrapper = mountCard(makeTask(), 'completed')
    expect(wrapper.find('.cancel-btn').exists()).toBe(false)
  })

  it('cancel button has title="取消任務"', () => {
    const wrapper = mountCard(makeTask())
    const btn = wrapper.find('.cancel-btn')
    expect(btn.attributes('title')).toBe('取消任務')
  })

  it('clicking cancel button calls ElMessageBox.confirm', async () => {
    const { ElMessageBox } = await import('element-plus')
    const confirmMock = vi.mocked(ElMessageBox.confirm)

    const wrapper = mountCard(makeTask({ sn: 42 }))
    const btn = wrapper.find('.cancel-btn')
    await btn.trigger('click')
    // Flush promise microtasks for the async handler.
    await Promise.resolve()

    expect(confirmMock).toHaveBeenCalled()
  })

  it('confirmed cancel calls cancelTask with the task sn', async () => {
    const { ElMessageBox } = await import('element-plus')
    vi.mocked(ElMessageBox.confirm).mockResolvedValue('confirm' as never)

    const clientModule = await import('@/api/client')
    const cancelTaskMock = vi.mocked(clientModule.cancelTask)

    const wrapper = mountCard(makeTask({ sn: 99 }))
    const btn = wrapper.find('.cancel-btn')
    await btn.trigger('click')
    // Flush multiple microtask ticks.
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()

    expect(cancelTaskMock).toHaveBeenCalledWith(99)
  })
})

// ---------------------------------------------------------------------------
// Truncation + tooltip on long bangumi names
// ---------------------------------------------------------------------------

describe('TaskCard — long bangumi_name: tooltip + truncation', () => {
  it('renders title inside el-tooltip with content equal to the full formatted title', () => {
    const longName = 'This Is An Extremely Long Bangumi Name That Should Trigger Truncation Effect'
    expect(longName.length).toBeGreaterThan(50)

    const wrapper = mountCard(makeTask({ bangumi_name: longName, episode: '5', filename: 'ep5.mp4' }))

    // The el-tooltip stub exposes content via data-content attribute.
    const tooltip = wrapper.find('.el-tooltip[data-content]')
    expect(tooltip.exists()).toBe(true)
    // The computed title wraps with 《》and appends the episode.
    const expectedTitle = `《${longName}》 - EP 5`
    expect(tooltip.attributes('data-content')).toBe(expectedTitle)
  })

  it('renders title text inside the task-card__title span (within tooltip)', () => {
    const longName = 'Another Very Long Anime Series Name For Testing Ellipsis Truncation Behavior'
    expect(longName.length).toBeGreaterThan(50)

    const wrapper = mountCard(makeTask({ bangumi_name: longName, episode: undefined }))

    const titleSpan = wrapper.find('.task-card__title')
    expect(titleSpan.exists()).toBe(true)
    expect(titleSpan.text()).toContain(longName)
  })

  it('tooltip content equals filename when bangumi_name is absent', () => {
    const wrapper = mountCard(makeTask({ bangumi_name: undefined, filename: 'fallback-file.mp4' }))

    const tooltip = wrapper.find('.el-tooltip[data-content]')
    expect(tooltip.exists()).toBe(true)
    expect(tooltip.attributes('data-content')).toBe('fallback-file.mp4')
  })
})
