import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import MonitorTable from '@/components/monitor/MonitorTable.vue'
import type { TaskProgressEntry } from '@/types'
import { filterByOwner, filterBySource, filterByStatus } from '@/utils/monitorTable'
import { createElementPlusStubs } from '../../helpers/elementPlusStubs'

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

vi.mock('@/api/tasks', async (importOriginal) => {
  const mod = (await importOriginal()) as Record<string, unknown>
  return {
    ...mod,
    TasksApi: vi.fn().mockImplementation(() => ({
      dismissTask: dismissTaskMock,
    })),
  }
})

const stubs = createElementPlusStubs()

function makeTask(overrides: Partial<TaskProgressEntry> = {}): TaskProgressEntry {
  return {
    sn: 1,
    rate: 50,
    status: '正在下載',
    filename: 'Episode 01.mp4',
    ...overrides,
  }
}

function mountTable(tasks: TaskProgressEntry[], dimmed = false) {
  return mount(MonitorTable, {
    props: { tasks, dimmed },
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

describe('MonitorTable — columns', () => {
  it('test_table_renders_all_expected_columns', () => {
    const wrapper = mountTable([makeTask()])
    const headers = wrapper.findAll('.el-table-header-cell').map((h) => h.attributes('data-label'))

    expect(headers).toEqual([
      '作品',
      '集',
      '來源',
      '狀態',
      '進度',
      '速度',
      'ETA',
      '擁有者',
      '動作',
    ])
  })

  it('shows 集/速度/ETA/擁有者 as em-dash placeholders when null', () => {
    const wrapper = mountTable([
      makeTask({ episode: undefined, speed_mbps: null, eta_seconds: null, owner_username: undefined }),
    ])
    const text = wrapper.text()
    // Four distinct em-dash cells (集, 速度, ETA, 擁有者).
    expect(text.match(/—/g)?.length).toBeGreaterThanOrEqual(4)
  })

  it('formats speed and ETA for a fully populated row', () => {
    const wrapper = mountTable([makeTask({ speed_mbps: 12.34, eta_seconds: 425 })])
    const text = wrapper.text()
    expect(text).toContain('12.3 MB/s')
    expect(text).toContain('07:05')
  })
})

describe('MonitorTable — owner avatar', () => {
  it('passes owner_avatar_url through to el-avatar as :src', () => {
    const wrapper = mountTable([
      makeTask({
        owner_username: 'edward',
        owner_avatar_url: 'https://cdn.discordapp.com/avatars/1/abc.png',
      }),
    ])
    const avatar = wrapper.findComponent(stubs.ElAvatar) as unknown as AvatarWrapper
    expect(avatar.props('src')).toBe('https://cdn.discordapp.com/avatars/1/abc.png')
  })

  it('falls back to initials when owner_avatar_url is null', () => {
    const wrapper = mountTable([makeTask({ owner_username: 'edward', owner_avatar_url: null })])
    const avatar = wrapper.findComponent(stubs.ElAvatar) as unknown as AvatarWrapper
    expect(avatar.props('src')).toBeFalsy()
    expect(avatar.text()).toBe('ED')
  })
})

describe('MonitorTable — default sort', () => {
  it('test_table_sorts_by_started_at_desc_by_default', () => {
    const wrapper = mountTable([
      makeTask({ sn: 1, started_at: '2026-07-11T10:00:00Z' }),
      makeTask({ sn: 2, started_at: null }),
      makeTask({ sn: 3, started_at: '2026-07-11T12:00:00Z' }),
      makeTask({ sn: 4, started_at: '2026-07-11T11:00:00Z' }),
    ])

    const rowSns = wrapper.findAll('.el-table-row').map((row) => row.attributes('data-sn'))
    // Newest task first; the null-started_at row sinks to the bottom
    // regardless of "descending" direction (it is "no data", not "oldest").
    expect(rowSns).toEqual(['3', '4', '1', '2'])
  })
})

describe('MonitorTable — filter predicates', () => {
  it('test_table_filters_by_source_when_column_filter_applied', () => {
    const btRow = makeTask({ source: 'bt' })
    const animadRowExplicit = makeTask({ source: 'animad' })
    const unknownRowImplicit = makeTask({ source: undefined })
    const bilibiliRow = makeTask({ source: 'bilibili' })

    expect(filterBySource(btRow, 'bt')).toBe(true)
    expect(filterBySource(animadRowExplicit, 'bt')).toBe(false)
    expect(filterBySource(animadRowExplicit, 'animad')).toBe(true)
    // Absent source normalizes to the '' sentinel — a distinct bucket from
    // 'animad', not a synonym for it (see monitorTable.ts's filterBySource).
    expect(filterBySource(unknownRowImplicit, '')).toBe(true)
    expect(filterBySource(unknownRowImplicit, 'animad')).toBe(false)
    expect(filterBySource(bilibiliRow, 'animad')).toBe(false)
  })

  it('filterByStatus matches on the exact Chinese status string', () => {
    expect(filterByStatus(makeTask({ status: '正在下載' }), '正在下載')).toBe(true)
    expect(filterByStatus(makeTask({ status: '等待下載' }), '正在下載')).toBe(false)
  })

  it('filterByOwner matches on the exact owner_username', () => {
    expect(filterByOwner(makeTask({ owner_username: 'edward' }), 'edward')).toBe(true)
    expect(filterByOwner(makeTask({ owner_username: 'someone-else' }), 'edward')).toBe(false)
  })
})

describe('MonitorTable — empty state', () => {
  it('test_table_shows_empty_state_when_no_rows', () => {
    const wrapper = mountTable([])
    const empty = wrapper.find('.el-empty')
    expect(empty.exists()).toBe(true)
    expect(empty.text()).toContain('目前沒有任務')
    expect(wrapper.find('.el-table').exists()).toBe(false)
  })
})

describe('MonitorTable — source badge', () => {
  it('test_bt_source_row_renders_orange_badge', () => {
    const wrapper = mountTable([makeTask({ source: 'bt' })])
    const badge = wrapper.find('.el-tag[data-source="bt"]')
    expect(badge.exists()).toBe(true)
    expect(badge.attributes('data-color')).toBe('#e6a23c')
    expect(badge.text()).toBe('BT')
  })

  it('renders a distinct badge for animad vs bilibili', () => {
    const wrapper = mountTable([
      makeTask({ sn: 1, source: 'animad' }),
      makeTask({ sn: 2, source: 'bilibili' }),
    ])
    const animadBadge = wrapper.find('.el-tag[data-source="animad"]')
    const bilibiliBadge = wrapper.find('.el-tag[data-source="bilibili"]')
    expect(animadBadge.attributes('data-color')).toBe('#3b8686')
    expect(bilibiliBadge.attributes('data-color')).toBe('#00a1d6')
  })
})

describe('MonitorTable — dimmed state', () => {
  it('applies the dimmed class when disconnected with stale data', () => {
    const wrapper = mountTable([makeTask()], true)
    expect(wrapper.find('.monitor-table--dimmed').exists()).toBe(true)
  })

  it('does not apply the dimmed class when connected', () => {
    const wrapper = mountTable([makeTask()], false)
    expect(wrapper.find('.monitor-table--dimmed').exists()).toBe(false)
  })
})

describe('MonitorTable — cancel action', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    dismissTaskMock.mockResolvedValue({ status: 'ok' })
  })

  it('renders a cancel button for an active (non-completed) row', () => {
    const wrapper = mountTable([makeTask({ status: '正在下載' })])
    expect(wrapper.find('.cancel-btn').exists()).toBe(true)
  })

  it('hides the cancel button for a completed row', () => {
    const wrapper = mountTable([makeTask({ status: '下載完成' })])
    expect(wrapper.find('.cancel-btn').exists()).toBe(false)
  })

  it('test_x_button_calls_dismiss_api_not_the_legacy_cancel_flow', async () => {
    const wrapper = mountTable([makeTask({ sn: 77 })])
    await wrapper.find('.cancel-btn').trigger('click')
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
    expect(dismissTaskMock).toHaveBeenCalledWith(77)
  })

  it('clicking the button does not open a confirm dialog — dismiss is immediate', async () => {
    const { ElMessageBox } = await import('element-plus')
    const confirmMock = vi.mocked(ElMessageBox.confirm)
    const wrapper = mountTable([makeTask()])
    await wrapper.find('.cancel-btn').trigger('click')
    await Promise.resolve()
    expect(confirmMock).not.toHaveBeenCalled()
  })

  it('shows a success toast after a successful dismiss', async () => {
    const { ElMessage } = await import('element-plus')
    const successMock = vi.mocked(ElMessage.success)
    const wrapper = mountTable([makeTask({ sn: 7 })])
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
    const wrapper = mountTable([makeTask({ sn: 8 })])
    await wrapper.find('.cancel-btn').trigger('click')
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
    expect(errorMock).toHaveBeenCalled()
  })
})
