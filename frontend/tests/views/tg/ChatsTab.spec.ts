/**
 * Unit tests for ChatsTab.vue — watched-chat list plus the "選擇要監控的 Chat"
 * picker dialog's search + category filter (added alongside the flat list).
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createElementPlusStubs, elementPlusModuleMock } from '../../helpers/elementPlusStubs'
import type { TgAvailableChat, TgWatchedChat } from '@/types'

const mockListChats = vi.fn()
const mockListAvailableChats = vi.fn()
const mockCreateChat = vi.fn()
const mockUpdateChat = vi.fn()
const mockDeleteChat = vi.fn()
const mockRetryBackfill = vi.fn()

vi.mock('@/api/tg', () => ({
  TgApi: vi.fn().mockImplementation(() => ({
    listChats: mockListChats,
    listAvailableChats: mockListAvailableChats,
    createChat: mockCreateChat,
    updateChat: mockUpdateChat,
    deleteChat: mockDeleteChat,
    retryBackfill: mockRetryBackfill,
  })),
}))

const isMobileRef = ref(false)

vi.mock('@/composables/useBreakpoint', () => ({
  useBreakpoint: () => ({
    isMobile: isMobileRef,
    isTablet: ref(false),
  }),
}))

const { mockElMessageError, mockElMessageSuccess } = vi.hoisted(() => ({
  mockElMessageError: vi.fn(),
  mockElMessageSuccess: vi.fn(),
}))

vi.mock('element-plus', () =>
  elementPlusModuleMock({
    ElMessage: { success: mockElMessageSuccess, error: mockElMessageError, warning: vi.fn(), info: vi.fn() },
    ElMessageBox: { confirm: vi.fn().mockResolvedValue(undefined), alert: vi.fn(), prompt: vi.fn() },
  }),
)

import ChatsTab from '@/views/tg/ChatsTab.vue'

const stubs = createElementPlusStubs()

function makeAvailableChat(overrides: Partial<TgAvailableChat> = {}): TgAvailableChat {
  return {
    chat_id: 1,
    title: 'Anime Fansub Group',
    type: 'supergroup',
    already_watched: false,
    ...overrides,
  }
}

function makeWatchedChat(overrides: Partial<TgWatchedChat> = {}): TgWatchedChat {
  return {
    id: 1,
    chat_id: 1,
    chat_title: 'Anime Fansub Group',
    media_types: ['video'],
    size_min_mb: null,
    size_max_mb: null,
    format_whitelist: null,
    save_path: null,
    enabled: true,
    created_at: '2026-01-01T00:00:00+00:00',
    backfill_enabled: false,
    backfill_days: 7,
    backfill_status: null,
    backfill_scanned_count: 0,
    backfill_matched_count: 0,
    backfill_started_at: null,
    backfill_finished_at: null,
    ...overrides,
  }
}

function mountView() {
  return mount(ChatsTab, {
    global: { stubs },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  isMobileRef.value = false
  mockListChats.mockResolvedValue([])
  mockListAvailableChats.mockResolvedValue([
    makeAvailableChat({ chat_id: 1, title: 'Anime Fansub Group', type: 'supergroup' }),
    makeAvailableChat({ chat_id: 2, title: 'Release Bot', type: 'bot' }),
    makeAvailableChat({ chat_id: 3, title: 'Personal Notes', type: 'private' }),
    makeAvailableChat({ chat_id: 4, title: 'Announcements', type: 'channel' }),
  ])
})

async function openPickerDialog(wrapper: ReturnType<typeof mountView>) {
  const openBtn = wrapper.findAll('button').find((b) => b.text().includes('新增監控 Chat'))
  expect(openBtn).toBeDefined()
  await openBtn!.trigger('click')
  await flushPromises()
}

describe('ChatsTab — watched chat list', () => {
  it('renders already-watched chats and suppresses the default el-table empty text', async () => {
    mockListChats.mockResolvedValue([makeWatchedChat({ chat_title: 'Anime Fansub Group' })])
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('Anime Fansub Group')
    // Fix 3 — only the outer custom empty message should ever render, never
    // el-table's own default "暫無資料" alongside it.
    expect(wrapper.text()).not.toContain('暫無資料')
  })

  it('shows only the outer empty message (not el-table\'s default) when there are no watched chats', async () => {
    mockListChats.mockResolvedValue([])
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('目前沒有監控中的 Chat')
    expect(wrapper.text()).not.toContain('暫無資料')
  })
})

describe('ChatsTab — picker dialog search + category filter', () => {
  it('renders a search input and a category filter above the list', async () => {
    const wrapper = mountView()
    await flushPromises()
    await openPickerDialog(wrapper)

    expect(wrapper.find('input.ag-picker-search').exists()).toBe(true)
    expect(wrapper.find('select.ag-picker-category').exists()).toBe(true)
  })

  it('shows every available chat before any filter is applied', async () => {
    const wrapper = mountView()
    await flushPromises()
    await openPickerDialog(wrapper)

    const items = wrapper.findAll('.ag-picker-item')
    expect(items).toHaveLength(4)
  })

  it('filters by title, case-insensitive, after a 200ms debounce', async () => {
    vi.useFakeTimers()
    try {
      const wrapper = mountView()
      await flushPromises()
      await openPickerDialog(wrapper)

      const searchInput = wrapper.find('input.ag-picker-search')
      await searchInput.setValue('fansub')

      // Not yet applied — debounce still pending.
      vi.advanceTimersByTime(199)
      await flushPromises()
      expect(wrapper.findAll('.ag-picker-item')).toHaveLength(4)

      vi.advanceTimersByTime(1)
      await flushPromises()
      const items = wrapper.findAll('.ag-picker-item')
      expect(items).toHaveLength(1)
      expect(items[0]!.text()).toContain('Anime Fansub Group')
    } finally {
      vi.useRealTimers()
    }
  })

  it('filters by category', async () => {
    const wrapper = mountView()
    await flushPromises()
    await openPickerDialog(wrapper)

    const categorySelect = wrapper.find('select.ag-picker-category')
    await categorySelect.setValue('bot')
    await flushPromises()

    const items = wrapper.findAll('.ag-picker-item')
    expect(items).toHaveLength(1)
    expect(items[0]!.text()).toContain('Release Bot')
  })

  it('shows "沒有符合條件的 Chat" when the filter matches nothing', async () => {
    vi.useFakeTimers()
    try {
      const wrapper = mountView()
      await flushPromises()
      await openPickerDialog(wrapper)

      const searchInput = wrapper.find('input.ag-picker-search')
      await searchInput.setValue('no such chat exists')
      vi.advanceTimersByTime(200)
      await flushPromises()

      expect(wrapper.text()).toContain('沒有符合條件的 Chat')
    } finally {
      vi.useRealTimers()
    }
  })

  it('resets filters each time the dialog reopens', async () => {
    const wrapper = mountView()
    await flushPromises()
    await openPickerDialog(wrapper)

    const categorySelect = wrapper.find('select.ag-picker-category')
    await categorySelect.setValue('bot')
    await flushPromises()
    expect(wrapper.findAll('.ag-picker-item')).toHaveLength(1)

    // Close and reopen — filters should reset to "全部".
    const vm = wrapper.vm as unknown as { pickerVisible: boolean }
    vm.pickerVisible = false
    await flushPromises()
    await openPickerDialog(wrapper)

    expect(wrapper.findAll('.ag-picker-item')).toHaveLength(4)
  })
})

describe('ChatsTab — picker dialog backfill add form', () => {
  it('renders the backfill checkbox and a disabled days input by default', async () => {
    const wrapper = mountView()
    await flushPromises()
    await openPickerDialog(wrapper)

    const checkbox = wrapper.find('.ag-picker-backfill-checkbox input[type="checkbox"]')
    expect(checkbox.exists()).toBe(true)
    expect((checkbox.element as HTMLInputElement).checked).toBe(false)

    const days = wrapper.find('input.ag-picker-backfill-days')
    expect(days.exists()).toBe(true)
    expect(days.attributes('disabled')).toBeDefined()
  })

  it('enables the days input once the checkbox is checked', async () => {
    const wrapper = mountView()
    await flushPromises()
    await openPickerDialog(wrapper)

    const checkbox = wrapper.find('.ag-picker-backfill-checkbox input[type="checkbox"]')
    await checkbox.setValue(true)

    const days = wrapper.find('input.ag-picker-backfill-days')
    expect(days.attributes('disabled')).toBeUndefined()
  })

  it('resets the backfill form on each reopen', async () => {
    const wrapper = mountView()
    await flushPromises()
    await openPickerDialog(wrapper)

    const checkbox = wrapper.find('.ag-picker-backfill-checkbox input[type="checkbox"]')
    await checkbox.setValue(true)
    const days = wrapper.find('input.ag-picker-backfill-days')
    await days.setValue(30)

    const vm = wrapper.vm as unknown as { pickerVisible: boolean }
    vm.pickerVisible = false
    await flushPromises()
    await openPickerDialog(wrapper)

    const reopenedCheckbox = wrapper.find('.ag-picker-backfill-checkbox input[type="checkbox"]')
    expect((reopenedCheckbox.element as HTMLInputElement).checked).toBe(false)
    expect((wrapper.find('input.ag-picker-backfill-days').element as HTMLInputElement).value).toBe('7')
  })

  it('sends backfill_enabled and backfill_days when picking a chat', async () => {
    mockCreateChat.mockResolvedValue(makeWatchedChat({ backfill_enabled: true, backfill_days: 30 }))
    const wrapper = mountView()
    await flushPromises()
    await openPickerDialog(wrapper)

    const checkbox = wrapper.find('.ag-picker-backfill-checkbox input[type="checkbox"]')
    await checkbox.setValue(true)
    const days = wrapper.find('input.ag-picker-backfill-days')
    await days.setValue(30)

    const item = wrapper.find('.ag-picker-item')
    await item.trigger('click')
    await flushPromises()

    expect(mockCreateChat).toHaveBeenCalledWith(
      expect.objectContaining({ backfill_enabled: true, backfill_days: 30 }),
    )
  })

  it('picking a chat without enabling backfill sends backfill_enabled: false', async () => {
    mockCreateChat.mockResolvedValue(makeWatchedChat())
    const wrapper = mountView()
    await flushPromises()
    await openPickerDialog(wrapper)

    const item = wrapper.find('.ag-picker-item')
    await item.trigger('click')
    await flushPromises()

    expect(mockCreateChat).toHaveBeenCalledWith(
      expect.objectContaining({ backfill_enabled: false, backfill_days: 7 }),
    )
  })
})

describe('ChatsTab — backfill status column', () => {
  it('renders nothing for a chat that never requested a backfill', async () => {
    mockListChats.mockResolvedValue([makeWatchedChat({ backfill_status: null })])
    const wrapper = mountView()
    await flushPromises()

    const row = wrapper.find('.el-table-row')
    expect(row.find('.el-tag').exists()).toBe(false)
  })

  it('renders the pending label', async () => {
    mockListChats.mockResolvedValue([makeWatchedChat({ backfill_status: 'pending' })])
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('回填排隊中')
  })

  it('renders running progress once scanned_count > 0', async () => {
    mockListChats.mockResolvedValue([
      makeWatchedChat({ backfill_status: 'running', backfill_scanned_count: 40, backfill_matched_count: 5 }),
    ])
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('回填中 5/40')
  })

  it('renders a generic running label before any message has been scanned', async () => {
    mockListChats.mockResolvedValue([
      makeWatchedChat({ backfill_status: 'running', backfill_scanned_count: 0, backfill_matched_count: 0 }),
    ])
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('回填中...')
  })

  it('renders the done summary with matched count', async () => {
    mockListChats.mockResolvedValue([
      makeWatchedChat({ backfill_status: 'done', backfill_matched_count: 12, backfill_scanned_count: 200 }),
    ])
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('回填完成，抓到 12 個檔案')
  })

  it('renders a failed tag with a retry button in the table row', async () => {
    mockListChats.mockResolvedValue([makeWatchedChat({ id: 5, backfill_status: 'failed' })])
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('回填失敗')
    const retryBtn = wrapper.findAll('button').find((b) => b.text().includes('重試回填'))
    expect(retryBtn).toBeDefined()

    mockRetryBackfill.mockResolvedValue(makeWatchedChat({ id: 5, backfill_status: 'pending' }))
    await retryBtn!.trigger('click')
    await flushPromises()

    expect(mockRetryBackfill).toHaveBeenCalledWith(5)
    expect(mockElMessageSuccess).toHaveBeenCalled()
  })

  it('does not show a retry button for a done or running chat', async () => {
    mockListChats.mockResolvedValue([
      makeWatchedChat({ id: 1, backfill_status: 'done' }),
      makeWatchedChat({ id: 2, chat_id: 2, backfill_status: 'running' }),
    ])
    const wrapper = mountView()
    await flushPromises()

    const retryBtn = wrapper.findAll('button').find((b) => b.text().includes('重試回填'))
    expect(retryBtn).toBeUndefined()
  })
})

describe('ChatsTab — edit dialog backfill controls', () => {
  async function openEdit(wrapper: ReturnType<typeof mountView>): Promise<void> {
    const editBtn = wrapper.findAll('button').find((b) => b.text().trim() === '編輯')
    expect(editBtn).toBeDefined()
    await editBtn!.trigger('click')
    await flushPromises()
  }

  it('pre-fills the backfill checkbox and days from the row', async () => {
    mockListChats.mockResolvedValue([makeWatchedChat({ backfill_enabled: true, backfill_days: 21 })])
    const wrapper = mountView()
    await flushPromises()
    await openEdit(wrapper)

    const checkbox = wrapper.find('.ag-edit-backfill-checkbox input[type="checkbox"]')
    expect((checkbox.element as HTMLInputElement).checked).toBe(true)
    expect((wrapper.find('input.ag-edit-backfill-days').element as HTMLInputElement).value).toBe('21')
  })

  it('saving includes backfill_enabled and backfill_days in the update payload', async () => {
    mockListChats.mockResolvedValue([makeWatchedChat({ id: 3, backfill_enabled: false, backfill_days: 7 })])
    mockUpdateChat.mockResolvedValue(makeWatchedChat({ id: 3, backfill_enabled: true, backfill_days: 45 }))
    const wrapper = mountView()
    await flushPromises()
    await openEdit(wrapper)

    const checkbox = wrapper.find('.ag-edit-backfill-checkbox input[type="checkbox"]')
    await checkbox.setValue(true)
    const days = wrapper.find('input.ag-edit-backfill-days')
    await days.setValue(45)

    const saveBtn = wrapper.findAll('button').find((b) => b.text().trim() === '儲存')
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(mockUpdateChat).toHaveBeenCalledWith(
      3,
      expect.objectContaining({ backfill_enabled: true, backfill_days: 45 }),
    )
  })

  it('shows a 重新回填 button for a done backfill and confirms before retrying', async () => {
    mockListChats.mockResolvedValue([makeWatchedChat({ id: 8, backfill_status: 'done', backfill_matched_count: 3 })])
    mockRetryBackfill.mockResolvedValue(makeWatchedChat({ id: 8, backfill_status: 'pending' }))
    const wrapper = mountView()
    await flushPromises()
    await openEdit(wrapper)

    expect(wrapper.text()).toContain('回填完成，抓到 3 個檔案')
    const rerunBtn = wrapper.findAll('button').find((b) => b.text().trim() === '重新回填')
    expect(rerunBtn).toBeDefined()
    await rerunBtn!.trigger('click')
    await flushPromises()

    expect(mockRetryBackfill).toHaveBeenCalledWith(8)
  })

  it('does not show a retry/re-run button when the chat never ran a backfill', async () => {
    mockListChats.mockResolvedValue([makeWatchedChat({ backfill_status: null })])
    const wrapper = mountView()
    await flushPromises()
    await openEdit(wrapper)

    const buttons = wrapper.findAll('button').map((b) => b.text().trim())
    expect(buttons).not.toContain('重試回填')
    expect(buttons).not.toContain('重新回填')
  })
})
