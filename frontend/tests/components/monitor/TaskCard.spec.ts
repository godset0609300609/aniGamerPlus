import { describe, expect, it, vi, afterEach, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import TaskCard from '@/components/monitor/TaskCard.vue'
import type { TaskProgressEntry } from '@/types'
type CardVariant = 'downloading' | 'waiting' | 'completed' | 'retry' | 'other'
import { createElementPlusStubs } from '../../helpers/elementPlusStubs'

// Module-level mocks (hoisted by Vitest automatically).
const { dismissTaskMock } = vi.hoisted(() => ({
  dismissTaskMock: vi.fn().mockResolvedValue({ status: 'ok' }),
}))

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

// TaskCard's X button now dismisses via TasksApi.dismissTask (a real backend
// call, POST /api/monitor/progress/{sn}/force-finish) rather than the old
// confirm+cancelTask flow — see src/utils/taskActions.ts's dismissTask().
vi.mock('@/api/tasks', async (importOriginal) => {
  const mod = (await importOriginal()) as Record<string, unknown>
  return {
    ...mod,
    TasksApi: vi.fn().mockImplementation(() => ({
      dismissTask: dismissTaskMock,
    })),
  }
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

/** Minimal shape for reading props off the ElAvatar stub — `findComponent`
 * with a plain stub definition (rather than a real SFC import) still types
 * as `WrapperLike`, so we narrow through `unknown` like FeedsTab.spec.ts
 * does for ElTableColumn. */
interface AvatarWrapper {
  props(name: string): unknown
  text(): string
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

  it('shows BT badge when source is bt', () => {
    const wrapper = mountCard(makeTask({ source: 'bt' }))
    const badge = wrapper.find('.task-card__badge--bt')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe('BT')
  })

  it('hides BT badge when source is animad/other', () => {
    const wrapper = mountCard(makeTask({ source: 'animad' }))
    expect(wrapper.find('.task-card__badge--bt').exists()).toBe(false)
  })

  it('hides BT badge when source is absent', () => {
    const wrapper = mountCard(makeTask({ source: undefined }))
    expect(wrapper.find('.task-card__badge--bt').exists()).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Source badge colors — shared mapping in src/utils/sourceBadge.ts.
// Colors are asserted via the `data-color` attribute (not the `style`
// attribute) because happy-dom/browsers normalize inline hex colors set
// through `element.style` to `rgb(...)` when serialized back out, which
// would make a literal-hex assertion on `style` environment-dependent.
// ---------------------------------------------------------------------------
describe('TaskCard — source badge colors', () => {
  it('test_source_badge_uses_bahamut_green_for_animad', () => {
    const wrapper = mountCard(makeTask({ source: 'animad' }))
    const badge = wrapper.find('.task-card__badge--animad')
    expect(badge.exists()).toBe(true)
    expect(badge.attributes('data-color')).toBe('#3b8686')
    expect(badge.text()).toBe('動畫瘋')
  })

  it('defaults the animad badge (and color) when source is absent', () => {
    const wrapper = mountCard(makeTask({ source: undefined }))
    const badge = wrapper.find('.task-card__badge--animad')
    expect(badge.exists()).toBe(true)
    expect(badge.attributes('data-color')).toBe('#3b8686')
  })

  it('test_source_badge_uses_bilibili_blue', () => {
    const wrapper = mountCard(makeTask({ source: 'bilibili' }))
    const badge = wrapper.find('.task-card__badge--bilibili')
    expect(badge.exists()).toBe(true)
    expect(badge.attributes('data-color')).toBe('#00a1d6')
    expect(badge.text()).toBe('Bilibili')
  })

  it('test_source_badge_uses_orange_for_bt', () => {
    const wrapper = mountCard(makeTask({ source: 'bt' }))
    const badge = wrapper.find('.task-card__badge--bt')
    expect(badge.exists()).toBe(true)
    expect(badge.attributes('data-color')).toBe('#e6a23c')
    expect(badge.text()).toBe('BT')
  })

  it('falls back to a neutral gray badge for an unrecognized source', () => {
    const wrapper = mountCard(makeTask({ source: 'mystery-source' }))
    const badge = wrapper.find('.task-card__badge--other')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe('mystery-source')
  })
})

// ---------------------------------------------------------------------------
// Owner avatar
// ---------------------------------------------------------------------------
describe('TaskCard — owner avatar', () => {
  it('shows an avatar with initials when owner_username is present', () => {
    const wrapper = mountCard(makeTask({ owner_username: 'edward' }))
    const avatar = wrapper.find('.task-card__avatar')
    expect(avatar.exists()).toBe(true)
    expect(avatar.text()).toBe('ED')
  })

  it('hides the avatar when owner_username is absent', () => {
    const wrapper = mountCard(makeTask({ owner_username: undefined }))
    expect(wrapper.find('.task-card__avatar').exists()).toBe(false)
  })

  it('passes owner_avatar_url through to el-avatar as :src', () => {
    const wrapper = mountCard(
      makeTask({ owner_username: 'edward', owner_avatar_url: 'https://cdn.discordapp.com/avatars/1/abc.png' }),
    )
    // Pass the stub's own component definition (not a CSS selector) so
    // Vue Test Utils resolves the actual ElAvatar instance rather than an
    // untyped DOM node.
    const avatar = wrapper.findComponent(stubs.ElAvatar) as unknown as AvatarWrapper
    expect(avatar.props('src')).toBe('https://cdn.discordapp.com/avatars/1/abc.png')
  })

  it('passes a null owner_avatar_url through so el-avatar falls back to initials', () => {
    const wrapper = mountCard(makeTask({ owner_username: 'edward', owner_avatar_url: null }))
    const avatar = wrapper.findComponent(stubs.ElAvatar) as unknown as AvatarWrapper
    expect(avatar.props('src')).toBeFalsy()
    expect(avatar.text()).toBe('ED')
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
// Batch H — dismiss ('X') button
// ---------------------------------------------------------------------------

describe('TaskCard — dismiss button', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    dismissTaskMock.mockResolvedValue({ status: 'ok' })
  })

  it('renders a dismiss button with class cancel-btn for downloading variant', () => {
    const wrapper = mountCard(makeTask(), 'downloading')
    expect(wrapper.find('.cancel-btn').exists()).toBe(true)
  })

  it('hides dismiss button when variant is completed', () => {
    const wrapper = mountCard(makeTask(), 'completed')
    expect(wrapper.find('.cancel-btn').exists()).toBe(false)
  })

  it('dismiss button has title="取消任務"', () => {
    const wrapper = mountCard(makeTask())
    const btn = wrapper.find('.cancel-btn')
    expect(btn.attributes('title')).toBe('取消任務')
  })

  it('test_x_button_calls_dismiss_api_not_just_local_removal', async () => {
    const wrapper = mountCard(makeTask({ sn: 42 }))
    const btn = wrapper.find('.cancel-btn')
    await btn.trigger('click')
    // Flush multiple microtask ticks for the async handler.
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()

    // Proves the click reaches the backend dismiss API (POST .../force-finish
    // via TasksApi.dismissTask) rather than just splicing the entry out of
    // local reactive state — a purely-local removal would never call this.
    expect(dismissTaskMock).toHaveBeenCalledWith(42)
  })

  it('clicking the button does not open a confirm dialog — dismiss is immediate', async () => {
    const { ElMessageBox } = await import('element-plus')
    const confirmMock = vi.mocked(ElMessageBox.confirm)

    const wrapper = mountCard(makeTask())
    await wrapper.find('.cancel-btn').trigger('click')
    await Promise.resolve()

    expect(confirmMock).not.toHaveBeenCalled()
  })

  it('shows a success toast after a successful dismiss', async () => {
    const { ElMessage } = await import('element-plus')
    const successMock = vi.mocked(ElMessage.success)

    const wrapper = mountCard(makeTask({ sn: 7 }))
    await wrapper.find('.cancel-btn').trigger('click')
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()

    expect(successMock).toHaveBeenCalled()
  })

  it('shows an error toast when the dismiss API call fails', async () => {
    const { ElMessage } = await import('element-plus')
    const errorMock = vi.mocked(ElMessage.error)
    dismissTaskMock.mockRejectedValueOnce(new Error('network down'))

    const wrapper = mountCard(makeTask({ sn: 8 }))
    await wrapper.find('.cancel-btn').trigger('click')
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()

    expect(errorMock).toHaveBeenCalled()
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
