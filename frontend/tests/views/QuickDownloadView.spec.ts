/**
 * Unit tests for QuickDownloadView.vue — the popup landing page opened by
 * the Tampermonkey userscript from a 動畫瘋 anime page, mirroring
 * QuickAddView.spec.ts's strategy but for the manual-download flow.
 *
 * Strategy:
 *  - Stub `useRoute` (vue-router) with a mutable `query` object.
 *  - Stub TasksApi.submitManual + ConfigApi.load so no real HTTP calls
 *    happen.
 *  - Spy on `window.close` since the view calls it after a successful
 *    submit.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import {
  createElementPlusStubs,
  elementPlusModuleMock,
} from '../helpers/elementPlusStubs'

// ---------------------------------------------------------------------------
// vue-router stub — mutable query object per test.
// ---------------------------------------------------------------------------
const mockRoute = { query: {} as Record<string, string | undefined> }
vi.mock('vue-router', () => ({
  useRoute: () => mockRoute,
  useRouter: () => ({ push: vi.fn() }),
}))

// ---------------------------------------------------------------------------
// TasksApi / ConfigApi stubs.
// ---------------------------------------------------------------------------
const mockSubmitManual = vi.fn()
const mockConfigLoad = vi.fn()

vi.mock('@/api/tasks', () => ({
  TasksApi: vi.fn().mockImplementation(() => ({
    submitManual: mockSubmitManual,
  })),
}))

vi.mock('@/api/config', () => ({
  ConfigApi: vi.fn().mockImplementation(() => ({
    load: mockConfigLoad,
  })),
}))

// ---------------------------------------------------------------------------
// Element Plus mock.
// ---------------------------------------------------------------------------
const { mockElMessageSuccess, mockElMessageError } = vi.hoisted(() => ({
  mockElMessageSuccess: vi.fn(),
  mockElMessageError: vi.fn(),
}))

vi.mock('element-plus', () =>
  elementPlusModuleMock({
    ElMessage: {
      success: mockElMessageSuccess,
      error: mockElMessageError,
      warning: vi.fn(),
      info: vi.fn(),
    },
  }),
)

// Import component AFTER mocks are set up.
import QuickDownloadView from '@/views/QuickDownloadView.vue'

const stubs = createElementPlusStubs()

function mountView() {
  return mount(QuickDownloadView, {
    global: { stubs },
  })
}

let closeSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers()
  mockRoute.query = {}
  mockSubmitManual.mockResolvedValue({ status: 'ok' })
  mockConfigLoad.mockResolvedValue({ download_resolution: '1080' })
  closeSpy = vi.spyOn(window, 'close').mockImplementation(() => {})
})

afterEach(() => {
  vi.useRealTimers()
  closeSpy.mockRestore()
})

describe('QuickDownloadView — dialog auto-open', () => {
  it('opens the dialog automatically when sn query is present', async () => {
    mockRoute.query = { sn: '12345' }

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('form').exists()).toBe(true)
    const snInput = wrapper.find('input[readonly]').element as HTMLInputElement
    expect(snInput.value).toBe('12345')
  })

  it('prefills the readonly name field from the title query param', async () => {
    mockRoute.query = { sn: '12345', title: '進擊的巨人' }

    const wrapper = mountView()
    await flushPromises()

    const nameInput = wrapper.findAll('input[readonly]').find((i) => (i.element as HTMLInputElement).value === '進擊的巨人')
    expect(nameInput).toBeTruthy()
  })
})

describe('QuickDownloadView — resolution defaults', () => {
  it('defaults the resolution select to 1080 before config loads', async () => {
    mockRoute.query = { sn: '12345' }
    // Never resolves within this test — the select must already show the
    // hardcoded '1080' default synchronously on dialog open.
    mockConfigLoad.mockReturnValue(new Promise(() => {}))

    const wrapper = mountView()
    await flushPromises()

    const select = wrapper.find('select.el-select').element as HTMLSelectElement
    expect(select.value).toBe('1080')
  })

  it('adopts the server-configured default resolution once config loads', async () => {
    mockRoute.query = { sn: '12345' }
    mockConfigLoad.mockResolvedValue({ download_resolution: '720' })

    const wrapper = mountView()
    await flushPromises()

    const select = wrapper.find('select.el-select').element as HTMLSelectElement
    expect(select.value).toBe('720')
  })

  it('falls back to 1080 when the config fetch fails', async () => {
    mockRoute.query = { sn: '12345' }
    mockConfigLoad.mockRejectedValue(new Error('network error'))

    const wrapper = mountView()
    await flushPromises()

    const select = wrapper.find('select.el-select').element as HTMLSelectElement
    expect(select.value).toBe('1080')
  })
})

describe('QuickDownloadView — submit success', () => {
  it('calls TasksApi.submitManual with sn+resolution and closes the window on success', async () => {
    mockRoute.query = { sn: '12345', title: '進擊的巨人' }
    mockConfigLoad.mockResolvedValue({ download_resolution: '720' })

    const wrapper = mountView()
    await flushPromises()

    const submitBtn = wrapper.findAll('button').find((b) => b.text().trim() === '下載')!
    await submitBtn.trigger('click')
    await flushPromises()

    expect(mockSubmitManual).toHaveBeenCalledTimes(1)
    expect(mockSubmitManual).toHaveBeenCalledWith(
      expect.objectContaining({ sn: '12345', resolution: '720' }),
    )

    expect(mockElMessageSuccess).toHaveBeenCalledWith(expect.stringContaining('進擊的巨人'))

    expect(closeSpy).not.toHaveBeenCalled()
    vi.advanceTimersByTime(800)
    expect(closeSpy).toHaveBeenCalledTimes(1)
  })

  it('falls back to sn= label in the toast when no title is present', async () => {
    mockRoute.query = { sn: '999' }

    const wrapper = mountView()
    await flushPromises()

    const submitBtn = wrapper.findAll('button').find((b) => b.text().trim() === '下載')!
    await submitBtn.trigger('click')
    await flushPromises()

    expect(mockElMessageSuccess).toHaveBeenCalledWith(expect.stringContaining('sn=999'))
  })
})

describe('QuickDownloadView — submit failure', () => {
  it('shows an error and keeps the dialog open when submit fails', async () => {
    mockRoute.query = { sn: '12345' }
    mockSubmitManual.mockRejectedValue(new Error('network error'))

    const wrapper = mountView()
    await flushPromises()

    const submitBtn = wrapper.findAll('button').find((b) => b.text().trim() === '下載')!
    await submitBtn.trigger('click')
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalled()
    vi.advanceTimersByTime(1000)
    expect(closeSpy).not.toHaveBeenCalled()

    // Dialog form should still be present for a retry.
    const retryBtn = wrapper.findAll('button').find((b) => b.text().trim() === '下載')
    expect(retryBtn).toBeTruthy()
  })
})

describe('QuickDownloadView — cancel', () => {
  it('closes the window without submitting on cancel', async () => {
    mockRoute.query = { sn: '12345' }

    const wrapper = mountView()
    await flushPromises()

    const cancelBtn = wrapper.findAll('button').find((b) => b.text().trim() === '取消')!
    await cancelBtn.trigger('click')

    expect(mockSubmitManual).not.toHaveBeenCalled()
    expect(closeSpy).toHaveBeenCalledTimes(1)
  })
})

describe('QuickDownloadView — missing sn query', () => {
  it('shows the error card when sn query is missing', async () => {
    mockRoute.query = {}

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('此頁面需要從動畫瘋透過擴充啟動')
    // No form fields should be rendered.
    expect(wrapper.find('.el-select').exists()).toBe(false)
  })
})

describe('QuickDownloadView — no owner picker', () => {
  it('never renders an owner select (manual tasks have no owner override)', async () => {
    mockRoute.query = { sn: '12345' }

    const wrapper = mountView()
    await flushPromises()

    // Only one el-select present: 解析度.
    const selects = wrapper.findAll('.el-select')
    expect(selects).toHaveLength(1)
  })
})
