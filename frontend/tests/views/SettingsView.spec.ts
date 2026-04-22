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
// useTelegramBinding stub — prevents real fetch calls in these tests.
// ---------------------------------------------------------------------------
vi.mock('@/composables/useTelegramBinding', () => ({
  useTelegramBinding: () => ({
    bound: ref(false),
    notifyEnabled: ref(true),
    linkPending: ref(false),
    notConfigured: ref(false),
    loading: ref(false),
    error: ref(null),
    countdownLabel: ref('0:00'),
    secondsRemaining: ref(0),
    loadStatus: vi.fn().mockResolvedValue(undefined),
    startLink: vi.fn().mockResolvedValue(undefined),
    unlink: vi.fn().mockResolvedValue(undefined),
    setNotifyEnabled: vi.fn().mockResolvedValue(undefined),
    dispose: vi.fn(),
  }),
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
const { mockElMessageSuccess, mockElMessageError, mockElMessageInfo, mockElMessageBoxConfirm } =
  vi.hoisted(() => ({
    mockElMessageSuccess: vi.fn(),
    mockElMessageError: vi.fn(),
    mockElMessageInfo: vi.fn(),
    mockElMessageBoxConfirm: vi.fn(),
  }))

vi.mock('element-plus', () =>
  elementPlusModuleMock({
    ElMessage: {
      success: mockElMessageSuccess,
      error: mockElMessageError,
      warning: vi.fn(),
      info: mockElMessageInfo,
    },
    ElMessageBox: {
      confirm: mockElMessageBoxConfirm,
      alert: vi.fn(),
      prompt: vi.fn(),
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
    telegram: {
      enabled: false,
      bot_token: '',
      webhook_secret: '',
      public_url: '',
      notify_on: ['completed', 'failed', 'cancelled'],
      admin_broadcast: true,
      rate_limit_per_minute: 30,
    },
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
  mockElMessageBoxConfirm.mockResolvedValue(undefined)
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
  it('non-admin does NOT see the cookie section', async () => {
    isAdminRef.value = false
    const wrapper = mountView()
    await flushPromises()

    const inputs = wrapper.findAll('input.el-input')
    const cookieInput = inputs.find(
      (i) => i.attributes('placeholder') === '貼上完整 cookie 字串',
    )
    expect(cookieInput).toBeUndefined()
  })

  it('non-admin does NOT see the save button (cookie)', async () => {
    isAdminRef.value = false
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const saveBtn = buttons.find((b) => b.text().includes('儲存'))
    expect(saveBtn).toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// Settings load / save / discard (DirtyFab interactions)
// ---------------------------------------------------------------------------

describe('SettingsView — load on mount', () => {
  it('calls api.load on mount and renders form fields', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(mockLoad).toHaveBeenCalledTimes(1)
    // After load, settings are non-null so the form is rendered (not skeleton).
    expect(wrapper.find('form').exists()).toBe(true)
  })

  it('calls api.getCookieStatus on mount', async () => {
    mountView()
    await flushPromises()

    expect(mockGetCookieStatus).toHaveBeenCalledTimes(1)
  })

  it('survives getCookieStatus rejection without throwing', async () => {
    mockGetCookieStatus.mockRejectedValue(new Error('status fetch failed'))
    const wrapper = mountView()
    await flushPromises()

    // Form is still rendered; status badge defaults to false.
    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.text()).toContain('尚未設定')
  })
})

describe('SettingsView — save settings', () => {
  it('calls api.save with merged settings when DirtyFab save is emitted', async () => {
    const wrapper = mountView()
    await flushPromises()

    // Directly call save() via the component's internal vm.
    const vm = wrapper.vm as unknown as { save: () => Promise<void> }
    await vm.save()
    await flushPromises()

    expect(mockSave).toHaveBeenCalledTimes(1)
    expect(mockElMessageSuccess).toHaveBeenCalledWith('配置已成功提交')
  })

  it('shows error message when api.save rejects', async () => {
    mockSave.mockRejectedValueOnce(new Error('save failed'))
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { save: () => Promise<void> }
    await vm.save()
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalledWith(expect.stringContaining('save failed'))
  })

  it('calls load() again after save succeeds (snapshot refresh)', async () => {
    const wrapper = mountView()
    await flushPromises()

    const callsBefore = mockLoad.mock.calls.length
    const vm = wrapper.vm as unknown as { save: () => Promise<void> }
    await vm.save()
    await flushPromises()

    // load() is called once more after save.
    expect(mockLoad.mock.calls.length).toBeGreaterThan(callsBefore)
  })
})

describe('SettingsView — reload (discard)', () => {
  it('calls load() and shows info message when ElMessageBox confirms', async () => {
    mockElMessageBoxConfirm.mockResolvedValueOnce(undefined)

    const wrapper = mountView()
    await flushPromises()

    const callsBefore = mockLoad.mock.calls.length
    const vm = wrapper.vm as unknown as { reload: () => Promise<void> }
    await vm.reload()
    await flushPromises()

    expect(mockLoad.mock.calls.length).toBeGreaterThan(callsBefore)
    expect(mockElMessageInfo).toHaveBeenCalledWith('配置已重載')
  })

  it('does not reload when ElMessageBox confirm is cancelled', async () => {
    mockElMessageBoxConfirm.mockRejectedValueOnce('cancel')

    const wrapper = mountView()
    await flushPromises()

    const callsBefore = mockLoad.mock.calls.length
    const vm = wrapper.vm as unknown as { reload: () => Promise<void> }
    await vm.reload()
    await flushPromises()

    // load() count must not have increased.
    expect(mockLoad.mock.calls.length).toBe(callsBefore)
  })
})

describe('SettingsView — fillCurrentUA', () => {
  it('fills settings.ua with navigator.userAgent and shows success message', async () => {
    const wrapper = mountView()
    await flushPromises()

    const fakeUA = 'TestBrowser/1.0'
    vi.stubGlobal('navigator', { ...window.navigator, userAgent: fakeUA })

    const vm = wrapper.vm as unknown as {
      fillCurrentUA: () => void
      settings: { ua: string }
    }
    vm.fillCurrentUA()
    await wrapper.vm.$nextTick()

    expect(vm.settings?.ua).toBe(fakeUA)
    expect(mockElMessageSuccess).toHaveBeenCalledWith('已取得當前瀏覽器 UA')
    vi.unstubAllGlobals()
  })
})

describe('SettingsView — dirty computed', () => {
  it('dirty is false immediately after load', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { dirty: boolean }
    expect(vm.dirty).toBe(false)
  })

  it('dirty becomes true after mutating a settings field', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      dirty: boolean
      settings: { bangumi_dir: string } | null
    }
    if (vm.settings) vm.settings.bangumi_dir = '/new/path'
    await wrapper.vm.$nextTick()
    expect(vm.dirty).toBe(true)
  })
})
