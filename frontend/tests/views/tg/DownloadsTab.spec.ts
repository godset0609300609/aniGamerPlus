/**
 * Unit tests for DownloadsTab.vue — the download list plus the per-item
 * 強制重新下載 action (confirmation, pending state, error surfacing).
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createElementPlusStubs, elementPlusModuleMock } from '../../helpers/elementPlusStubs'
import type { TgDownloadedMedia } from '@/types'

const mockListDownloads = vi.fn()
const mockForceRedownload = vi.fn()

vi.mock('@/api/tg', () => ({
  TgApi: vi.fn().mockImplementation(() => ({
    listDownloads: mockListDownloads,
    forceRedownload: mockForceRedownload,
  })),
}))

const isMobileRef = ref(false)

vi.mock('@/composables/useBreakpoint', () => ({
  useBreakpoint: () => ({
    isMobile: isMobileRef,
    isTablet: ref(false),
  }),
}))

const { mockElMessageError, mockElMessageSuccess, mockConfirm } = vi.hoisted(() => ({
  mockElMessageError: vi.fn(),
  mockElMessageSuccess: vi.fn(),
  mockConfirm: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('element-plus', () =>
  elementPlusModuleMock({
    ElMessage: { success: mockElMessageSuccess, error: mockElMessageError, warning: vi.fn(), info: vi.fn() },
    ElMessageBox: { confirm: mockConfirm, alert: vi.fn(), prompt: vi.fn() },
  }),
)

import DownloadsTab from '@/views/tg/DownloadsTab.vue'

const stubs = createElementPlusStubs()

function makeDownload(overrides: Partial<TgDownloadedMedia> = {}): TgDownloadedMedia {
  return {
    id: 1,
    chat_id: -100123,
    chat_title: '測試頻道',
    message_id: 42,
    file_name: 'episode01.mp4',
    file_size: 123_456_789,
    downloaded_at: '2026-01-01T00:00:00+00:00',
    local_path: 'episode01.mp4',
    ...overrides,
  }
}

function mountView() {
  return mount(DownloadsTab, {
    global: { stubs },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  isMobileRef.value = false
  mockConfirm.mockResolvedValue(undefined)
  mockListDownloads.mockResolvedValue({ items: [], total: 0, page: 1, size: 50 })
})

function findRedownloadButton(wrapper: ReturnType<typeof mountView>) {
  return wrapper.findAll('button').find((b) => b.text().includes('強制重新下載'))
}

describe('DownloadsTab — list', () => {
  it('renders downloaded items', async () => {
    mockListDownloads.mockResolvedValue({ items: [makeDownload()], total: 1, page: 1, size: 50 })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('episode01.mp4')
  })

  it('shows the empty state when there are no downloads', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('還沒有任何下載紀錄')
  })
})

describe('DownloadsTab — 強制重新下載', () => {
  it('renders a 強制重新下載 button per row', async () => {
    mockListDownloads.mockResolvedValue({ items: [makeDownload()], total: 1, page: 1, size: 50 })
    const wrapper = mountView()
    await flushPromises()

    expect(findRedownloadButton(wrapper)).toBeDefined()
  })

  it('asks for confirmation before calling the API', async () => {
    mockListDownloads.mockResolvedValue({ items: [makeDownload()], total: 1, page: 1, size: 50 })
    mockForceRedownload.mockResolvedValue({ entry_id: 1, status: 'queued' })
    const wrapper = mountView()
    await flushPromises()

    await findRedownloadButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(mockConfirm).toHaveBeenCalledOnce()
    expect(mockConfirm.mock.calls[0]![0]).toContain('episode01.mp4')
    expect(mockForceRedownload).toHaveBeenCalledWith(1)
  })

  it('does not call the API when the confirmation is cancelled', async () => {
    mockListDownloads.mockResolvedValue({ items: [makeDownload()], total: 1, page: 1, size: 50 })
    mockConfirm.mockRejectedValue(new Error('cancel'))
    const wrapper = mountView()
    await flushPromises()

    await findRedownloadButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(mockForceRedownload).not.toHaveBeenCalled()
  })

  it('shows a success message once the job is queued', async () => {
    mockListDownloads.mockResolvedValue({ items: [makeDownload()], total: 1, page: 1, size: 50 })
    mockForceRedownload.mockResolvedValue({ entry_id: 1, status: 'queued' })
    const wrapper = mountView()
    await flushPromises()

    await findRedownloadButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(mockElMessageSuccess).toHaveBeenCalledWith('已加入重新下載佇列')
  })

  it('surfaces an API error via ElMessage.error', async () => {
    mockListDownloads.mockResolvedValue({ items: [makeDownload()], total: 1, page: 1, size: 50 })
    mockForceRedownload.mockRejectedValue(new Error('下載紀錄不存在'))
    const wrapper = mountView()
    await flushPromises()

    await findRedownloadButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalledWith('強制重新下載失敗：下載紀錄不存在')
  })

  it('disables the button while the request is in flight, then re-enables it', async () => {
    mockListDownloads.mockResolvedValue({ items: [makeDownload()], total: 1, page: 1, size: 50 })
    let resolveRedownload!: (value: { entry_id: number; status: 'queued' }) => void
    mockForceRedownload.mockReturnValue(
      new Promise((resolve) => {
        resolveRedownload = resolve
      }),
    )
    const wrapper = mountView()
    await flushPromises()

    const button = findRedownloadButton(wrapper)!
    await button.trigger('click')
    await flushPromises()

    expect(findRedownloadButton(wrapper)!.attributes('disabled')).toBeDefined()

    resolveRedownload({ entry_id: 1, status: 'queued' })
    await flushPromises()

    expect(findRedownloadButton(wrapper)!.attributes('disabled')).toBeUndefined()
  })

  it('renders the action on mobile cards too', async () => {
    isMobileRef.value = true
    mockListDownloads.mockResolvedValue({ items: [makeDownload()], total: 1, page: 1, size: 50 })
    const wrapper = mountView()
    await flushPromises()

    expect(findRedownloadButton(wrapper)).toBeDefined()
  })
})
