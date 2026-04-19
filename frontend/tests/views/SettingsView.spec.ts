/**
 * Unit tests for SettingsView.vue — cookie field behaviour.
 *
 * Strategy: stub ConfigApi and useAuthStore so the component can be mounted
 * without a real HTTP server or auth session.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import {
  createElementPlusStubs,
  elementPlusModuleMock,
} from '../helpers/elementPlusStubs'

// ---------------------------------------------------------------------------
// Auth store stub — controllable isAdmin ref.
// ---------------------------------------------------------------------------
const isAdminRef = ref(true)

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ isAdmin: isAdminRef }),
}))

// ---------------------------------------------------------------------------
// ConfigApi stub — all methods resolved by default.
// ---------------------------------------------------------------------------
const mockLoad = vi.fn()
const mockSave = vi.fn()
const mockSetCookie = vi.fn()
const mockGetCookieStatus = vi.fn()

vi.mock('@/api/config', () => ({
  ConfigApi: vi.fn().mockImplementation(() => ({
    load: mockLoad,
    save: mockSave,
    setCookie: mockSetCookie,
    getCookieStatus: mockGetCookieStatus,
  })),
  parseProxy: vi.fn().mockReturnValue({
    protocol: 'HTTP',
    ip: '',
    port: '',
    user: '',
    password: '',
  }),
  serializeProxy: vi.fn().mockReturnValue(''),
}))

// ---------------------------------------------------------------------------
// Element Plus mock (imperatives need to be spy-able).
// vi.hoisted() ensures the fns exist before vi.mock() hoisting runs.
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
import SettingsView from '@/views/SettingsView.vue'

const stubs = createElementPlusStubs()

function defaultSettings() {
  return {
    bangumi_dir: '',
    temp_dir: '',
    classify_bangumi: true,
    lock_resolution: false,
    segment_download_mode: true,
    add_bangumi_name_to_video_filename: true,
    add_resolution_to_video_filename: true,
    download_resolution: '1080',
    default_download_mode: 'latest',
    check_frequency: 5,
    'multi-thread': 1,
    multi_downloading_segment: 2,
    customized_video_filename_prefix: '',
    customized_video_filename_suffix: '',
    ua: '',
    use_mobile_api: false,
    danmu: false,
    use_proxy: false,
    proxy: '',
    read_sn_list_when_checking_update: true,
    read_config_when_checking_update: true,
    save_logs: true,
    quantity_of_logs: 7,
    download_cd: 60,
    parse_sn_cd: 5,
    parse_cd: 3,
  }
}

function mountView() {
  return mount(SettingsView, {
    global: {
      stubs: {
        ...stubs,
        DirtyFab: { template: '<div class="dirty-fab-stub" />' },
      },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  isAdminRef.value = true
  mockLoad.mockResolvedValue(defaultSettings())
  mockSave.mockResolvedValue({ status: 'ok' })
  mockSetCookie.mockResolvedValue(undefined)
  mockGetCookieStatus.mockResolvedValue({ configured: false })
})

// ---------------------------------------------------------------------------
// Cookie field — rendering
// ---------------------------------------------------------------------------

describe('SettingsView — cookie field rendering', () => {
  it('renders a password input for the cookie draft', async () => {
    const wrapper = mountView()
    await flushPromises()

    // The cookie input has type="password" — stub renders it with class el-input.
    // We look for the input whose placeholder matches.
    const inputs = wrapper.findAll('input.el-input')
    const cookieInput = inputs.find(
      (i) => i.attributes('placeholder') === '貼上完整 cookie 字串',
    )
    expect(cookieInput).toBeDefined()
  })

  it('shows "尚未設定" tag when status.configured is false', async () => {
    mockGetCookieStatus.mockResolvedValue({ configured: false })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('尚未設定')
  })

  it('shows "目前已設定" tag when status.configured is true', async () => {
    mockGetCookieStatus.mockResolvedValue({ configured: true })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('目前已設定')
  })

  it('displays the hint text about admin-only and no display after save', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('Cookie 只有管理員能修改')
  })
})

// ---------------------------------------------------------------------------
// Cookie field — submit behaviour
// ---------------------------------------------------------------------------

describe('SettingsView — cookie submit', () => {
  it('calls api.setCookie with the draft value on button click', async () => {
    const wrapper = mountView()
    await flushPromises()

    // Set a draft value into the cookie input.
    const inputs = wrapper.findAll('input.el-input')
    const cookieInput = inputs.find(
      (i) => i.attributes('placeholder') === '貼上完整 cookie 字串',
    )
    expect(cookieInput).toBeDefined()
    await cookieInput!.setValue('BAHAMUT_SESSID=test123')

    // Find the save button labelled '儲存' and click it.
    const buttons = wrapper.findAll('button')
    const saveBtn = buttons.find((b) => b.text().includes('儲存'))
    expect(saveBtn).toBeDefined()
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(mockSetCookie).toHaveBeenCalledWith('BAHAMUT_SESSID=test123')
  })

  it('clears the draft and sets status to configured after successful save', async () => {
    const wrapper = mountView()
    await flushPromises()

    const inputs = wrapper.findAll('input.el-input')
    const cookieInput = inputs.find(
      (i) => i.attributes('placeholder') === '貼上完整 cookie 字串',
    )!
    await cookieInput.setValue('BAHAMUT=something')

    const buttons = wrapper.findAll('button')
    const saveBtn = buttons.find((b) => b.text().includes('儲存'))!
    await saveBtn.trigger('click')
    await flushPromises()

    // Draft cleared
    expect((cookieInput.element as HTMLInputElement).value).toBe('')
    // Success toast shown
    expect(mockElMessageSuccess).toHaveBeenCalledWith('Cookie 已更新')
    // Status now shows configured
    expect(wrapper.text()).toContain('目前已設定')
  })

  it('shows an error message when setCookie rejects', async () => {
    mockSetCookie.mockRejectedValue(new Error('network error'))

    const wrapper = mountView()
    await flushPromises()

    const inputs = wrapper.findAll('input.el-input')
    const cookieInput = inputs.find(
      (i) => i.attributes('placeholder') === '貼上完整 cookie 字串',
    )!
    await cookieInput.setValue('BAHAMUT=fail')

    const buttons = wrapper.findAll('button')
    const saveBtn = buttons.find((b) => b.text().includes('儲存'))!
    await saveBtn.trigger('click')
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalledWith(
      expect.stringContaining('network error'),
    )
  })
})

// ---------------------------------------------------------------------------
// Cookie field — downloader role
// ---------------------------------------------------------------------------

describe('SettingsView — downloader role restrictions', () => {
  it('disables the cookie input for a downloader user', async () => {
    isAdminRef.value = false
    const wrapper = mountView()
    await flushPromises()

    const inputs = wrapper.findAll('input.el-input')
    const cookieInput = inputs.find(
      (i) => i.attributes('placeholder') === '貼上完整 cookie 字串',
    )
    expect(cookieInput).toBeDefined()
    expect(cookieInput!.attributes('disabled')).toBeDefined()
  })

  it('disables the save button for a downloader user', async () => {
    isAdminRef.value = false
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const saveBtn = buttons.find((b) => b.text().includes('儲存'))
    expect(saveBtn).toBeDefined()
    expect(saveBtn!.attributes('disabled')).toBeDefined()
  })
})
