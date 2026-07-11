/**
 * Unit tests for SettingsView.vue — "BT 下載設定" section (bt-downloader
 * config fields + write-only Put.io token flow).
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createElementPlusStubs, elementPlusModuleMock } from '../helpers/elementPlusStubs'

interface NumberInputWrapper {
  props: (key: 'modelValue' | 'min') => number | undefined
}

const isAdminRef = ref(true)
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ isAdmin: isAdminRef }),
}))

// ---------------------------------------------------------------------------
// vue-router stub — SettingsView.vue reads/writes ?tab= via useRoute /
// useRouter. Mirrors the pattern used by BtView.spec.ts.
// ---------------------------------------------------------------------------
const mockRoute = { query: {} as Record<string, string | undefined> }
vi.mock('vue-router', () => ({
  useRoute: () => mockRoute,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

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

const mockLoad = vi.fn()
const mockSave = vi.fn()
const mockSetPutioToken = vi.fn()
const mockGetPutioTokenStatus = vi.fn()

vi.mock('@/api/config', () => ({
  ConfigApi: vi.fn().mockImplementation(() => ({
    load: mockLoad,
    save: mockSave,
    setCookie: vi.fn().mockResolvedValue(undefined),
    getCookieStatus: vi.fn().mockResolvedValue({ configured: false }),
    setBilibiliCookie: vi.fn().mockResolvedValue({ status: 'ok' }),
    getBilibiliCookieStatus: vi.fn().mockResolvedValue({ configured: false }),
    setPutioToken: mockSetPutioToken,
    getPutioTokenStatus: mockGetPutioTokenStatus,
    setTelegramBotToken: vi.fn().mockResolvedValue({ status: 'ok' }),
    getTelegramBotTokenStatus: vi.fn().mockResolvedValue({ configured: false }),
    setTelegramWebhookSecret: vi.fn().mockResolvedValue({ status: 'ok' }),
    getTelegramWebhookSecretStatus: vi.fn().mockResolvedValue({ configured: false }),
  })),
  parseProxy: vi.fn().mockReturnValue({ protocol: 'HTTP', ip: '', port: '', user: '', password: '' }),
  serializeProxy: vi.fn().mockReturnValue(''),
}))

// Prevents SettingsView.vue's Telegram 帳號 section (and the always-mounted
// TgBindDialog child) from making a real fetch to /api/tg/session on mount.
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
    ElMessageBox: { confirm: vi.fn().mockResolvedValue(undefined), alert: vi.fn(), prompt: vi.fn() },
  }),
)

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
 * Activates a tab by clicking its <el-tabs> nav item. The BT 下載設定
 * section (and its Put.io token flow) now lives under the BT 下載 tab
 * (id "bt") rather than the default 一般 tab.
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
  mockSetPutioToken.mockResolvedValue({ status: 'ok' })
  mockGetPutioTokenStatus.mockResolvedValue({ configured: false })
})

describe('SettingsView — BT 下載設定 section rendering', () => {
  it('renders the section title', async () => {
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'bt')

    expect(wrapper.text()).toContain('BT 下載設定')
  })

  it('renders the poll-interval-seconds input with the loaded value', async () => {
    mockLoad.mockResolvedValue({
      ...defaultSettings(),
      'bt-downloader': { ...defaultSettings()['bt-downloader'], 'poll-interval-seconds': 600 },
    })
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'bt')

    const inputs = wrapper.findAll('input.el-input-number')
    const match = inputs.find((i) => (i.element as HTMLInputElement).value === '600')
    expect(match).toBeDefined()
  })

  it('renders the landing-dir input with placeholder', async () => {
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'bt')

    const inputs = wrapper.findAll('input.el-input')
    const match = inputs.find((i) => i.attributes('placeholder') === '留空則使用番劇資料夾')
    expect(match).toBeDefined()
  })

  it('non-admin does NOT see the BT 下載設定 section', async () => {
    isAdminRef.value = false
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).not.toContain('BT 下載設定')
  })

  it('renders the entry-retention-days and task-history-retention-days inputs with loaded values', async () => {
    mockLoad.mockResolvedValue({
      ...defaultSettings(),
      'bt-downloader': {
        ...defaultSettings()['bt-downloader'],
        'entry-retention-days': 45,
        'task-history-retention-days': 200,
      },
    })
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'bt')

    const inputs = wrapper.findAll('input.el-input-number')
    const entryRetentionInput = inputs.find((i) => (i.element as HTMLInputElement).value === '45')
    const historyRetentionInput = inputs.find((i) => (i.element as HTMLInputElement).value === '200')
    expect(entryRetentionInput).toBeDefined()
    expect(historyRetentionInput).toBeDefined()
  })

  it('entry-retention-days and task-history-retention-days inputs enforce min=1', async () => {
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'bt')

    const numberInputs = wrapper.findAllComponents(stubs.ElInputNumber) as unknown as NumberInputWrapper[]
    const entryRetention = numberInputs.find(
      (c) => c.props('modelValue') === defaultSettings()['bt-downloader']['entry-retention-days'],
    )
    const historyRetention = numberInputs.find(
      (c) => c.props('modelValue') === defaultSettings()['bt-downloader']['task-history-retention-days'],
    )
    expect(entryRetention?.props('min')).toBe(1)
    expect(historyRetention?.props('min')).toBe(1)
  })

  it('renders the auto-delete-remote-on-landed switch reflecting the loaded value', async () => {
    mockLoad.mockResolvedValue({
      ...defaultSettings(),
      'bt-downloader': { ...defaultSettings()['bt-downloader'], 'auto-delete-remote-on-landed': false },
    })
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'bt')

    const vm = wrapper.vm as unknown as {
      settings: { 'bt-downloader': { 'auto-delete-remote-on-landed': boolean } } | null
    }
    expect(vm.settings?.['bt-downloader']['auto-delete-remote-on-landed']).toBe(false)

    const switches = wrapper.findAll('input.el-switch')
    const match = switches.find((s) => !(s.element as HTMLInputElement).checked)
    expect(match).toBeDefined()
  })
})

describe('SettingsView — BT 下載設定 auto-delete-remote-on-landed toggle + save', () => {
  it('toggling auto-delete-remote-on-landed marks the form dirty and is included on save', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      dirty: boolean
      settings: { 'bt-downloader': { 'auto-delete-remote-on-landed': boolean } } | null
      save: () => Promise<void>
    }
    expect(vm.dirty).toBe(false)
    if (vm.settings) vm.settings['bt-downloader']['auto-delete-remote-on-landed'] = false
    await wrapper.vm.$nextTick()
    expect(vm.dirty).toBe(true)

    await vm.save()
    await flushPromises()

    const savedArg = mockSave.mock.calls[0][0] as Record<string, unknown>
    const btDownloader = savedArg['bt-downloader'] as Record<string, unknown>
    expect(btDownloader['auto-delete-remote-on-landed']).toBe(false)
  })
})

describe('SettingsView — BT 下載設定 retention fields save', () => {
  it('submits modified entry-retention-days and task-history-retention-days as kebab-case keys', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      dirty: boolean
      settings: {
        'bt-downloader': {
          'entry-retention-days': number
          'task-history-retention-days': number
        }
      } | null
      save: () => Promise<void>
    }
    expect(vm.settings).not.toBeNull()
    if (vm.settings) {
      vm.settings['bt-downloader']['entry-retention-days'] = 45
      vm.settings['bt-downloader']['task-history-retention-days'] = 30
    }
    await wrapper.vm.$nextTick()

    await vm.save()
    await flushPromises()

    const savedArg = mockSave.mock.calls[0][0] as Record<string, unknown>
    const btDownloader = savedArg['bt-downloader'] as Record<string, unknown>
    expect(btDownloader['entry-retention-days']).toBe(45)
    expect(btDownloader['task-history-retention-days']).toBe(30)
  })
})

describe('SettingsView — BT 下載設定 dirty + save', () => {
  it('toggling enabled marks the form dirty and is included on save', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      dirty: boolean
      settings: { 'bt-downloader': { enabled: boolean } } | null
      save: () => Promise<void>
    }
    expect(vm.dirty).toBe(false)
    if (vm.settings) vm.settings['bt-downloader'].enabled = true
    await wrapper.vm.$nextTick()
    expect(vm.dirty).toBe(true)

    await vm.save()
    await flushPromises()

    const savedArg = mockSave.mock.calls[0][0] as Record<string, unknown>
    expect((savedArg['bt-downloader'] as { enabled: boolean }).enabled).toBe(true)
  })
})

describe('SettingsView — Put.io token', () => {
  it('calls getPutioTokenStatus on mount', async () => {
    mountView()
    await flushPromises()

    expect(mockGetPutioTokenStatus).toHaveBeenCalledTimes(1)
  })

  it('shows "尚未設定" when not configured', async () => {
    mockGetPutioTokenStatus.mockResolvedValue({ configured: false })
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'bt')

    expect(wrapper.text()).toContain('尚未設定')
  })

  it('shows "目前已設定" when configured', async () => {
    mockGetPutioTokenStatus.mockResolvedValue({ configured: true })
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'bt')

    expect(wrapper.text()).toContain('目前已設定')
  })

  it('survives getPutioTokenStatus rejection without throwing', async () => {
    mockGetPutioTokenStatus.mockRejectedValue(new Error('status fetch failed'))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('form').exists()).toBe(true)
  })

  it('calls setPutioToken with the draft value and clears it on success', async () => {
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'bt')

    const inputs = wrapper.findAll('input.el-input')
    const tokenInput = inputs.find((i) => i.attributes('placeholder') === '貼上 Put.io OAuth token')
    expect(tokenInput).toBeDefined()
    await tokenInput!.setValue('putio-oauth-abc123')

    // The BT 下載設定 section (and its Put.io save button) is the only
    // section rendered on the BT 下載 tab, so the first 儲存 match is ours.
    const buttons = wrapper.findAll('button')
    const saveBtn = buttons.find((b) => b.text().includes('儲存'))
    expect(saveBtn).toBeDefined()
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(mockSetPutioToken).toHaveBeenCalledWith('putio-oauth-abc123')
    expect(mockElMessageSuccess).toHaveBeenCalledWith('Put.io token 已更新')
    expect((tokenInput!.element as HTMLInputElement).value).toBe('')
  })

  it('shows an error message when setPutioToken rejects', async () => {
    mockSetPutioToken.mockRejectedValue(new Error('unauthorized'))

    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'bt')

    const inputs = wrapper.findAll('input.el-input')
    const tokenInput = inputs.find((i) => i.attributes('placeholder') === '貼上 Put.io OAuth token')!
    await tokenInput.setValue('bad-token')

    const buttons = wrapper.findAll('button')
    const saveBtn = buttons.find((b) => b.text().includes('儲存'))!
    await saveBtn.trigger('click')
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalledWith(expect.stringContaining('unauthorized'))
  })

  it('non-admin does NOT see the Put.io token input', async () => {
    isAdminRef.value = false
    const wrapper = mountView()
    await flushPromises()

    const inputs = wrapper.findAll('input.el-input')
    const tokenInput = inputs.find((i) => i.attributes('placeholder') === '貼上 Put.io OAuth token')
    expect(tokenInput).toBeUndefined()
  })
})
