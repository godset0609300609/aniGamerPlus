/**
 * Unit tests for SettingsView.vue — Telegram binding section.
 *
 * Stubs both ConfigApi and useTelegramBinding so this file is decoupled
 * from HTTP and the composable's internal timer logic.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createElementPlusStubs, elementPlusModuleMock } from '../helpers/elementPlusStubs'

// ---------------------------------------------------------------------------
// Auth stub
// ---------------------------------------------------------------------------
const isAdminRef = ref(true)
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ isAdmin: isAdminRef }),
}))

// ---------------------------------------------------------------------------
// ConfigApi stub
// ---------------------------------------------------------------------------
vi.mock('@/api/config', () => ({
  ConfigApi: vi.fn().mockImplementation(() => ({
    load: vi.fn().mockResolvedValue({
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
        enabled: true,
        bot_token: 'fake-token',
        webhook_secret: '',
        public_url: '',
        notify_on: ['completed', 'failed', 'cancelled'],
        admin_broadcast: true,
        rate_limit_per_minute: 30,
        allow_localhost: false,
      },
    }),
    save: vi.fn().mockResolvedValue({ status: 'ok' }),
    setCookie: vi.fn().mockResolvedValue(undefined),
    getCookieStatus: vi.fn().mockResolvedValue({ configured: false }),
  })),
  parseProxy: vi.fn().mockReturnValue({ protocol: 'HTTP', ip: '', port: '', user: '', password: '' }),
  serializeProxy: vi.fn().mockReturnValue(''),
}))

// ---------------------------------------------------------------------------
// useTelegramBinding stub — controllable refs
// ---------------------------------------------------------------------------
const tgBound = ref(false)
const tgNotifyEnabled = ref(true)
const tgLinkPending = ref(false)
const tgNotConfigured = ref(false)
const tgLoading = ref(false)
const tgError = ref<string | null>(null)
const tgCountdownLabel = ref('9:45')

const mockLoadStatus = vi.fn().mockResolvedValue(undefined)
const mockStartLink = vi.fn().mockResolvedValue(undefined)
const mockUnlink = vi.fn().mockResolvedValue(undefined)
const mockSetNotifyEnabled = vi.fn().mockResolvedValue(undefined)
const mockDispose = vi.fn()

vi.mock('@/composables/useTelegramBinding', () => ({
  useTelegramBinding: () => ({
    bound: tgBound,
    notifyEnabled: tgNotifyEnabled,
    linkPending: tgLinkPending,
    notConfigured: tgNotConfigured,
    loading: tgLoading,
    error: tgError,
    countdownLabel: tgCountdownLabel,
    secondsRemaining: ref(585),
    loadStatus: mockLoadStatus,
    startLink: mockStartLink,
    unlink: mockUnlink,
    setNotifyEnabled: mockSetNotifyEnabled,
    dispose: mockDispose,
  }),
}))

// ---------------------------------------------------------------------------
// Element Plus mock
// ---------------------------------------------------------------------------
const { mockElMessageSuccess, mockElMessageError, mockElMessageBoxConfirm } = vi.hoisted(() => ({
  mockElMessageSuccess: vi.fn(),
  mockElMessageError: vi.fn(),
  mockElMessageBoxConfirm: vi.fn(),
}))

vi.mock('element-plus', () =>
  elementPlusModuleMock({
    ElMessage: {
      success: mockElMessageSuccess,
      error: mockElMessageError,
      warning: vi.fn(),
      info: vi.fn(),
    },
    ElMessageBox: {
      confirm: mockElMessageBoxConfirm,
      alert: vi.fn(),
      prompt: vi.fn(),
    },
  }),
)

// ---------------------------------------------------------------------------
// telegram_admin API stub
// ---------------------------------------------------------------------------
vi.mock('@/api/telegram_admin', () => ({
  registerWebhook: vi.fn().mockResolvedValue({ ok: true, url: 'https://example.com/webhook' }),
  getWebhookInfo: vi.fn().mockResolvedValue({ url: null, pending_update_count: 0 }),
  getBotMe: vi.fn().mockResolvedValue({ id: 1, username: 'bot' }),
  deleteWebhook: vi.fn(),
}))

// Import component AFTER mocks are set up.
import SettingsView from '@/views/SettingsView.vue'

const stubs = createElementPlusStubs()

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
  tgBound.value = false
  tgNotifyEnabled.value = true
  tgLinkPending.value = false
  tgNotConfigured.value = false
  tgLoading.value = false
  tgError.value = null
  tgCountdownLabel.value = '9:45'
  mockLoadStatus.mockResolvedValue(undefined)
  mockStartLink.mockResolvedValue(undefined)
  mockUnlink.mockResolvedValue(undefined)
  mockSetNotifyEnabled.mockResolvedValue(undefined)
  mockElMessageBoxConfirm.mockResolvedValue(undefined)
})

// ---------------------------------------------------------------------------
// Telegram section — unbound state
// ---------------------------------------------------------------------------

describe('SettingsView Telegram — unbound', () => {
  it('renders "綁定 Telegram" button when not bound', async () => {
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const bindBtn = buttons.find((b) => b.text().includes('綁定 Telegram'))
    expect(bindBtn).toBeDefined()
  })

  it('clicking "綁定 Telegram" calls startLink', async () => {
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const bindBtn = buttons.find((b) => b.text().includes('綁定 Telegram'))
    expect(bindBtn).toBeDefined()
    await bindBtn!.trigger('click')
    await flushPromises()

    expect(mockStartLink).toHaveBeenCalledTimes(1)
  })

  it('shows notConfigured notice when telegram_not_configured', async () => {
    tgNotConfigured.value = true
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('系統管理員尚未設定 Telegram bot')
    const buttons = wrapper.findAll('button')
    const bindBtn = buttons.find((b) => b.text().includes('綁定 Telegram'))
    expect(bindBtn).toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// Telegram section — link pending state
// ---------------------------------------------------------------------------

describe('SettingsView Telegram — link pending', () => {
  beforeEach(() => {
    tgLinkPending.value = true
  })

  it('renders waiting state with countdown', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('等待 Telegram 確認')
    expect(wrapper.text()).toContain('9:45')
  })

  it('renders hint text', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('請到剛才開啟的 Telegram')
  })

  it('renders "取消綁定" button that calls unlink', async () => {
    mockUnlink.mockResolvedValue(undefined)
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const cancelBtn = buttons.find((b) => b.text().includes('取消綁定'))
    expect(cancelBtn).toBeDefined()
    await cancelBtn!.trigger('click')
    await flushPromises()

    expect(mockUnlink).toHaveBeenCalledTimes(1)
  })
})

// ---------------------------------------------------------------------------
// Telegram section — bound state
// ---------------------------------------------------------------------------

describe('SettingsView Telegram — bound', () => {
  beforeEach(() => {
    tgBound.value = true
  })

  it('renders "已綁定" when bound', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('已綁定')
  })

  it('renders notify toggle when bound', async () => {
    const wrapper = mountView()
    await flushPromises()

    const switches = wrapper.findAll('input.el-switch')
    expect(switches.length).toBeGreaterThan(0)
  })

  it('toggle change fires setNotifyEnabled', async () => {
    const wrapper = mountView()
    await flushPromises()

    // The ElSwitch stub emits 'update:modelValue' on change. Simulate by
    // finding the handler in the component and calling it directly via vm.
    const vm = wrapper.vm as unknown as { handleNotifyEnabledChange: (v: boolean) => Promise<void> }
    await vm.handleNotifyEnabledChange(false)
    await flushPromises()

    expect(mockSetNotifyEnabled).toHaveBeenCalledWith(false)
  })

  it('unlink button prompts confirmation dialog', async () => {
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const unlinkBtn = buttons.find((b) => b.text().includes('解除綁定'))
    expect(unlinkBtn).toBeDefined()
    await unlinkBtn!.trigger('click')
    await flushPromises()

    expect(mockElMessageBoxConfirm).toHaveBeenCalledTimes(1)
  })

  it('unlink proceeds when dialog confirmed', async () => {
    mockElMessageBoxConfirm.mockResolvedValue(undefined)
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const unlinkBtn = buttons.find((b) => b.text().includes('解除綁定'))
    await unlinkBtn!.trigger('click')
    await flushPromises()

    expect(mockUnlink).toHaveBeenCalledTimes(1)
    expect(mockElMessageSuccess).toHaveBeenCalledWith('已解除 Telegram 綁定')
  })

  it('does not call unlink when dialog cancelled', async () => {
    mockElMessageBoxConfirm.mockRejectedValue('cancel')
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const unlinkBtn = buttons.find((b) => b.text().includes('解除綁定'))
    await unlinkBtn!.trigger('click')
    await flushPromises()

    expect(mockUnlink).not.toHaveBeenCalled()
  })
})
