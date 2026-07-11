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
// vue-router stub — SettingsView.vue reads/writes ?tab= via useRoute /
// useRouter. Mirrors the pattern used by BtView.spec.ts.
// ---------------------------------------------------------------------------
const mockRoute = { query: {} as Record<string, string | undefined> }
const mockRouterReplace = vi.fn()
vi.mock('vue-router', () => ({
  useRoute: () => mockRoute,
  useRouter: () => ({ push: vi.fn(), replace: mockRouterReplace }),
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
const mockSetBilibiliCookie = vi.fn()
const mockGetBilibiliCookieStatus = vi.fn()
const mockSetPutioToken = vi.fn()
const mockGetPutioTokenStatus = vi.fn()
const mockSetTelegramBotToken = vi.fn()
const mockGetTelegramBotTokenStatus = vi.fn()
const mockSetTelegramWebhookSecret = vi.fn()
const mockGetTelegramWebhookSecretStatus = vi.fn()

vi.mock('@/api/config', () => ({
  ConfigApi: vi.fn().mockImplementation(() => ({
    load: mockLoad,
    save: mockSave,
    setCookie: mockSetCookie,
    getCookieStatus: mockGetCookieStatus,
    setBilibiliCookie: mockSetBilibiliCookie,
    getBilibiliCookieStatus: mockGetBilibiliCookieStatus,
    setPutioToken: mockSetPutioToken,
    getPutioTokenStatus: mockGetPutioTokenStatus,
    setTelegramBotToken: mockSetTelegramBotToken,
    getTelegramBotTokenStatus: mockGetTelegramBotTokenStatus,
    setTelegramWebhookSecret: mockSetTelegramWebhookSecret,
    getTelegramWebhookSecretStatus: mockGetTelegramWebhookSecretStatus,
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
// TgApi stub — prevents SettingsView.vue's Telegram 帳號 section (and the
// always-mounted TgBindDialog child) from making a real fetch on mount.
// ---------------------------------------------------------------------------
vi.mock('@/api/tg', () => ({
  TgApi: vi.fn().mockImplementation(() => ({
    getSessionStatus: vi.fn().mockResolvedValue({
      status: 'no_session',
      phone_tail4: null,
      telegram_user_id: null,
      telegram_handle: null,
      last_active_at: null,
      notification_bound: false,
    }),
    deleteSession: vi.fn().mockResolvedValue({ status: 'ok' }),
    startQrLogin: vi.fn(),
    pollQrLogin: vi.fn(),
    submitQrPassword: vi.fn(),
    startPhoneLogin: vi.fn(),
    submitPhoneCode: vi.fn(),
    submitPhonePassword: vi.fn(),
  })),
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
    'bilibili-concurrent-parts': 2,
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
      public_url: '',
      notify_on: ['completed', 'failed', 'cancelled'],
      admin_broadcast: true,
      rate_limit_per_minute: 30,
      health_alerts: true,
    },
    'bt-downloader': {
      enabled: false,
      'poll-interval-seconds': 300,
      'landing-poll-seconds': 60,
      'hanzi-convert': true,
      'landing-dir': '',
      'entry-retention-days': 90,
      'task-history-retention-days': 180,
      'auto-delete-remote-on-landed': true,
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

/**
 * Scope a "row" lookup to the `.cookie-row` (input + 儲存 button pair)
 * that holds the input with the given placeholder. The BT 下載設定
 * section also has a 儲存 button ahead of this one in the DOM, so a
 * bare `buttons.find(b => b.text().includes('儲存'))` is ambiguous.
 */
function findCookieRow(wrapper: ReturnType<typeof mountView>, placeholder: string) {
  const row = wrapper
    .findAll('.cookie-row')
    .find((r) => r.find('input.el-input').attributes('placeholder') === placeholder)
  if (!row) throw new Error(`no .cookie-row found for placeholder: ${placeholder}`)
  return row
}

/**
 * Activates a tab by clicking its <el-tabs> nav item. The Bahamut/Bilibili
 * cookie fields and the bilibili-concurrent-parts field now live under the
 * 來源 tab (id "source") rather than the default 一般 tab.
 */
async function switchTab(wrapper: ReturnType<typeof mountView>, tabId: string) {
  const nav = wrapper.find(`.el-tabs__item[data-name="${tabId}"]`)
  await nav.trigger('click')
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
  mockRoute.query = {}
  isAdminRef.value = true
  mockLoad.mockResolvedValue(defaultSettings())
  mockSave.mockResolvedValue({ status: 'ok' })
  mockSetCookie.mockResolvedValue(undefined)
  mockGetCookieStatus.mockResolvedValue({ configured: false })
  mockSetBilibiliCookie.mockResolvedValue({ status: 'ok' })
  mockGetBilibiliCookieStatus.mockResolvedValue({ configured: false })
  mockSetPutioToken.mockResolvedValue({ status: 'ok' })
  mockGetPutioTokenStatus.mockResolvedValue({ configured: false })
  mockSetTelegramBotToken.mockResolvedValue({ status: 'ok' })
  mockGetTelegramBotTokenStatus.mockResolvedValue({ configured: false })
  mockSetTelegramWebhookSecret.mockResolvedValue({ status: 'ok' })
  mockGetTelegramWebhookSecretStatus.mockResolvedValue({ configured: false })
  mockElMessageBoxConfirm.mockResolvedValue(undefined)
})

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

describe('SettingsView — tabs', () => {
  it('renders <el-tabs> with a nav item per visible tab', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('.el-tabs').exists()).toBe(true)
    const labels = wrapper.findAll('.ag-settings-tabs .el-tabs__item').map((n) => n.text())
    expect(labels).toEqual(['一般', '來源', 'BT 下載', 'Telegram'])
  })

  it('defaults to the 一般 tab', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { activeTab: string }
    expect(vm.activeTab).toBe('general')
    expect(wrapper.text()).toContain('路徑設定')
  })

  it('mounts with the tab from ?tab= active (deep link)', async () => {
    mockRoute.query = { tab: 'bt' }
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { activeTab: string }
    expect(vm.activeTab).toBe('bt')
    expect(wrapper.text()).toContain('BT 下載設定')

    const btNav = wrapper.find('.el-tabs__item[data-name="bt"]')
    expect(btNav.classes()).toContain('is-active')
  })

  it('falls back to the first visible tab when ?tab= is unknown', async () => {
    mockRoute.query = { tab: 'does-not-exist' }
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { activeTab: string }
    expect(vm.activeTab).toBe('general')
  })

  it('switching tabs updates the ?tab= query param via router.replace', async () => {
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'source')

    expect(mockRouterReplace).toHaveBeenCalledWith({ path: '/settings', query: { tab: 'source' } })
  })

  it('admin-only tabs (一般 / 來源 / BT 下載) are hidden entirely for non-admin', async () => {
    isAdminRef.value = false
    const wrapper = mountView()
    await flushPromises()

    const labels = wrapper.findAll('.ag-settings-tabs .el-tabs__item').map((n) => n.text())
    expect(labels).toEqual(['Telegram'])

    const vm = wrapper.vm as unknown as { activeTab: string }
    expect(vm.activeTab).toBe('telegram')
  })

  it('dirty state survives switching tabs (form data lives on the parent scope)', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      dirty: boolean
      settings: { bangumi_dir: string } | null
    }
    if (vm.settings) vm.settings.bangumi_dir = '/edited/path'
    await wrapper.vm.$nextTick()
    expect(vm.dirty).toBe(true)

    await switchTab(wrapper, 'bt')
    expect(vm.dirty).toBe(true)
    expect(vm.settings?.bangumi_dir).toBe('/edited/path')

    await switchTab(wrapper, 'general')
    expect(vm.dirty).toBe(true)
    expect(vm.settings?.bangumi_dir).toBe('/edited/path')
  })
})

// ---------------------------------------------------------------------------
// Cookie field — rendering
// ---------------------------------------------------------------------------

describe('SettingsView — cookie field rendering', () => {
  it('renders a password input for the cookie draft', async () => {
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'source')

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
    await switchTab(wrapper, 'source')

    expect(wrapper.text()).toContain('尚未設定')
  })

  it('shows "目前已設定" tag when status.configured is true', async () => {
    mockGetCookieStatus.mockResolvedValue({ configured: true })
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'source')

    expect(wrapper.text()).toContain('目前已設定')
  })

  it('displays the hint text about admin-only and no display after save', async () => {
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'source')

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
    await switchTab(wrapper, 'source')

    // Set a draft value into the cookie input.
    const inputs = wrapper.findAll('input.el-input')
    const cookieInput = inputs.find(
      (i) => i.attributes('placeholder') === '貼上完整 cookie 字串',
    )
    expect(cookieInput).toBeDefined()
    await cookieInput!.setValue('BAHAMUT_SESSID=test123')

    // Find the save button labelled '儲存' and click it.
    const saveBtn = findCookieRow(wrapper, '貼上完整 cookie 字串').find('button')
    expect(saveBtn.exists()).toBe(true)
    await saveBtn.trigger('click')
    await flushPromises()

    expect(mockSetCookie).toHaveBeenCalledWith('BAHAMUT_SESSID=test123')
  })

  it('clears the draft and sets status to configured after successful save', async () => {
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'source')

    const inputs = wrapper.findAll('input.el-input')
    const cookieInput = inputs.find(
      (i) => i.attributes('placeholder') === '貼上完整 cookie 字串',
    )!
    await cookieInput.setValue('BAHAMUT=something')

    const saveBtn = findCookieRow(wrapper, '貼上完整 cookie 字串').find('button')
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
    await switchTab(wrapper, 'source')

    const inputs = wrapper.findAll('input.el-input')
    const cookieInput = inputs.find(
      (i) => i.attributes('placeholder') === '貼上完整 cookie 字串',
    )!
    await cookieInput.setValue('BAHAMUT=fail')

    const saveBtn = findCookieRow(wrapper, '貼上完整 cookie 字串').find('button')
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

  it('calls api.getBilibiliCookieStatus on mount', async () => {
    mountView()
    await flushPromises()

    expect(mockGetBilibiliCookieStatus).toHaveBeenCalledTimes(1)
  })

  it('survives getBilibiliCookieStatus rejection without throwing', async () => {
    mockGetBilibiliCookieStatus.mockRejectedValue(new Error('bilibili status fetch failed'))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('form').exists()).toBe(true)
  })

  it('survives getCookieStatus rejection without throwing', async () => {
    mockGetCookieStatus.mockRejectedValue(new Error('status fetch failed'))
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'source')

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

// ---------------------------------------------------------------------------
// Bilibili concurrent parts field
// ---------------------------------------------------------------------------

describe('SettingsView — bilibili-concurrent-parts field', () => {
  it('renders the Bilibili concurrent parts input with the loaded value', async () => {
    mockLoad.mockResolvedValue({ ...defaultSettings(), 'bilibili-concurrent-parts': 3 })
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'source')

    const inputs = wrapper.findAll('input.el-input-number')
    const partsInput = inputs.find((i) => (i.element as HTMLInputElement).value === '3')
    expect(partsInput).toBeDefined()
  })

  it('includes updated bilibili-concurrent-parts value when save is called', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      save: () => Promise<void>
      settings: { 'bilibili-concurrent-parts': number } | null
    }
    if (vm.settings) vm.settings['bilibili-concurrent-parts'] = 4
    await wrapper.vm.$nextTick()

    await vm.save()
    await flushPromises()

    expect(mockSave).toHaveBeenCalledTimes(1)
    const savedArg = mockSave.mock.calls[0][0] as Record<string, unknown>
    expect(savedArg['bilibili-concurrent-parts']).toBe(4)
  })
})
