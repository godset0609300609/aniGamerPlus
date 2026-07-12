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
// vue-router stub — SettingsView.vue reads/writes ?tab= via useRoute /
// useRouter. Mirrors the pattern used by BtView.spec.ts.
// ---------------------------------------------------------------------------
const mockRoute = { query: {} as Record<string, string | undefined> }
vi.mock('vue-router', () => ({
  useRoute: () => mockRoute,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
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
        enabled: true,
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
    }),
    save: vi.fn().mockResolvedValue({ status: 'ok' }),
    setCookie: vi.fn().mockResolvedValue(undefined),
    getCookieStatus: vi.fn().mockResolvedValue({ configured: false }),
    setBilibiliCookie: vi.fn().mockResolvedValue({ status: 'ok' }),
    getBilibiliCookieStatus: vi.fn().mockResolvedValue({ configured: false }),
    setPutioToken: vi.fn().mockResolvedValue({ status: 'ok' }),
    getPutioTokenStatus: vi.fn().mockResolvedValue({ configured: false }),
    setTelegramBotToken: vi.fn().mockResolvedValue({ status: 'ok' }),
    getTelegramBotTokenStatus: vi.fn().mockResolvedValue({ configured: false }),
    setTelegramWebhookSecret: vi.fn().mockResolvedValue({ status: 'ok' }),
    getTelegramWebhookSecretStatus: vi.fn().mockResolvedValue({ configured: false }),
  })),
  parseProxy: vi.fn().mockReturnValue({ protocol: 'HTTP', ip: '', port: '', user: '', password: '' }),
  serializeProxy: vi.fn().mockReturnValue(''),
}))

// ---------------------------------------------------------------------------
// TgApi stub — prevents SettingsView.vue's Telegram 帳號 section (and the
// always-mounted TgBindDialog child) from making a real fetch on mount.
// getSessionStatus/deleteSession are controllable per-test so the merged
// status-label matrix (未綁定/僅通知已綁定/完整綁定/通知失敗/已失效) can be exercised.
// ---------------------------------------------------------------------------
const { mockGetSessionStatus, mockDeleteSession, mockRebindNotification } = vi.hoisted(() => ({
  mockGetSessionStatus: vi.fn(),
  mockDeleteSession: vi.fn(),
  mockRebindNotification: vi.fn(),
}))

vi.mock('@/api/tg', () => ({
  TgApi: vi.fn().mockImplementation(() => ({
    getSessionStatus: mockGetSessionStatus,
    deleteSession: mockDeleteSession,
    rebindNotification: mockRebindNotification,
    startQrLogin: vi.fn(),
    pollQrLogin: vi.fn(),
    submitQrPassword: vi.fn(),
    startPhoneLogin: vi.fn(),
    submitPhoneCode: vi.fn(),
    submitPhonePassword: vi.fn(),
  })),
}))

const NO_SESSION = {
  status: 'no_session',
  phone_tail4: null,
  telegram_user_id: null,
  telegram_handle: null,
  last_active_at: null,
  notification_bound: false,
}

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
  // Every test in this file exercises the Telegram section, which now lives
  // under its own tab (id "telegram") rather than the default 一般 tab —
  // deep-link straight into it via ?tab= instead of clicking through nav.
  mockRoute.query = { tab: 'telegram' }
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
  mockGetSessionStatus.mockResolvedValue({ ...NO_SESSION })
  mockDeleteSession.mockResolvedValue({ status: 'ok' })
  mockRebindNotification.mockResolvedValue({ notification_bind_status: 'success', notification_bind_error: null })
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

  it('clicking "綁定 Telegram" opens the TgBindDialog (not the legacy startLink flow)', async () => {
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const bindBtn = buttons.find((b) => b.text().includes('綁定 Telegram'))
    expect(bindBtn).toBeDefined()
    await bindBtn!.trigger('click')
    await flushPromises()

    // The merged section replaces the old direct-/start bind flow with the
    // QR/phone TgBindDialog — startLink is never called from the UI anymore.
    expect(mockStartLink).not.toHaveBeenCalled()
    const vm = wrapper.vm as unknown as { tgBindDialogVisible: boolean }
    expect(vm.tgBindDialogVisible).toBe(true)
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

// ---------------------------------------------------------------------------
// Merged status matrix — the account (User API) state and the notification
// (Bot API /start) state combine into one of five computed labels.
// ---------------------------------------------------------------------------

describe('SettingsView Telegram — merged status matrix', () => {
  it('shows 未綁定 when neither account nor notification is bound', async () => {
    tgBound.value = false
    mockGetSessionStatus.mockResolvedValue({ ...NO_SESSION })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('未綁定')
  })

  it('shows 僅通知已綁定 with an upgrade prompt when only the legacy /start bind exists', async () => {
    tgBound.value = true
    mockGetSessionStatus.mockResolvedValue({ ...NO_SESSION })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('僅通知已綁定')
    expect(wrapper.text()).toContain('升級為完整綁定')
  })

  it('shows 完整綁定 when both account and notification are bound', async () => {
    tgBound.value = true
    mockGetSessionStatus.mockResolvedValue({
      status: 'active',
      phone_tail4: null,
      telegram_user_id: 123,
      telegram_handle: 'alice',
      last_active_at: '2026-07-10T12:00:00Z',
      notification_bound: true,
    })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('完整綁定')
    expect(wrapper.text()).toContain('@alice')
    expect(wrapper.text()).toContain('2026-07-10T12:00:00Z')
    // Fully bound — no primary bind/upgrade button, only 解除綁定.
    const buttons = wrapper.findAll('button')
    expect(buttons.find((b) => b.text().includes('綁定 Telegram'))).toBeUndefined()
    expect(buttons.find((b) => b.text().includes('解除綁定'))).toBeDefined()
  })

  it('shows 帳號已綁定，通知綁定失敗 with a tooltip when the account is bound but auto-/start failed', async () => {
    tgBound.value = false
    mockGetSessionStatus.mockResolvedValue({
      status: 'active',
      phone_tail4: null,
      telegram_user_id: 123,
      telegram_handle: 'alice',
      last_active_at: null,
      notification_bound: false,
    })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('帳號已綁定，通知綁定失敗')
    expect(wrapper.text()).toContain('重新綁定通知')
    expect(wrapper.html()).toContain('Bot Username 可能未設定或錯誤')
  })

  it('tooltip shows the specific reason from notification_bind_status when present', async () => {
    tgBound.value = false
    mockGetSessionStatus.mockResolvedValue({
      status: 'active',
      phone_tail4: null,
      telegram_user_id: 123,
      telegram_handle: 'alice',
      last_active_at: null,
      notification_bound: false,
      notification_bind_status: 'bot_not_found',
      notification_bind_error: 'USERNAME_INVALID',
    })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.html()).toContain('找不到該 bot，請確認 Bot Username 正確')
    expect(wrapper.html()).not.toContain('Bot Username 可能未設定或錯誤')
  })

  it('tooltip includes the raw detail for telegram_error/unknown_error statuses', async () => {
    tgBound.value = false
    mockGetSessionStatus.mockResolvedValue({
      status: 'active',
      phone_tail4: null,
      telegram_user_id: 123,
      telegram_handle: 'alice',
      last_active_at: null,
      notification_bound: false,
      notification_bind_status: 'unknown_error',
      notification_bind_error: 'connection reset',
    })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.html()).toContain('未知錯誤: connection reset')
  })

  it('shows 已失效 with a rebind action when the session was revoked', async () => {
    tgBound.value = false
    mockGetSessionStatus.mockResolvedValue({
      status: 'revoked',
      phone_tail4: '1234',
      telegram_user_id: 123,
      telegram_handle: null,
      last_active_at: null,
      notification_bound: false,
    })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('已失效')
    const buttons = wrapper.findAll('button')
    expect(buttons.find((b) => b.text().includes('重新綁定 Telegram'))).toBeDefined()
  })

  it('解除綁定 routes to deleteSession (account) when the account is active', async () => {
    tgBound.value = true
    mockGetSessionStatus.mockResolvedValue({
      status: 'active',
      phone_tail4: null,
      telegram_user_id: 123,
      telegram_handle: 'alice',
      last_active_at: null,
      notification_bound: true,
    })
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const unbindBtn = buttons.find((b) => b.text().includes('解除綁定'))
    await unbindBtn!.trigger('click')
    await flushPromises()

    expect(mockDeleteSession).toHaveBeenCalledTimes(1)
    expect(mockUnlink).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Retry notification bind — "重試通知綁定" button, only shown for the
// "帳號已綁定，通知綁定失敗" merged status.
// ---------------------------------------------------------------------------

describe('SettingsView Telegram — retry notification bind', () => {
  function notifyFailedSession(overrides: Record<string, unknown> = {}) {
    return {
      status: 'active',
      phone_tail4: null,
      telegram_user_id: 123,
      telegram_handle: 'alice',
      last_active_at: null,
      notification_bound: false,
      notification_bind_status: 'bot_username_not_configured',
      notification_bind_error: null,
      ...overrides,
    }
  }

  it('does not render the retry button for other statuses', async () => {
    tgBound.value = true
    mockGetSessionStatus.mockResolvedValue({
      status: 'active',
      phone_tail4: null,
      telegram_user_id: 123,
      telegram_handle: 'alice',
      last_active_at: null,
      notification_bound: true,
    })
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    expect(buttons.find((b) => b.text().includes('重試通知綁定'))).toBeUndefined()
  })

  it('renders the retry button when notification bind failed', async () => {
    tgBound.value = false
    mockGetSessionStatus.mockResolvedValue(notifyFailedSession())
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    expect(buttons.find((b) => b.text().includes('重試通知綁定'))).toBeDefined()
  })

  it('clicking retry calls rebindNotification and reloads session status', async () => {
    tgBound.value = false
    mockGetSessionStatus.mockResolvedValue(notifyFailedSession())
    mockRebindNotification.mockResolvedValue({ notification_bind_status: 'success', notification_bind_error: null })
    const wrapper = mountView()
    await flushPromises()
    mockGetSessionStatus.mockResolvedValue({ ...notifyFailedSession(), notification_bind_status: 'success' })

    const buttons = wrapper.findAll('button')
    const retryBtn = buttons.find((b) => b.text().includes('重試通知綁定'))
    expect(retryBtn).toBeDefined()
    await retryBtn!.trigger('click')
    await flushPromises()

    expect(mockRebindNotification).toHaveBeenCalledTimes(1)
    // getSessionStatus is called once on mount and again after the retry.
    expect(mockGetSessionStatus).toHaveBeenCalledTimes(2)
    expect(mockElMessageSuccess).toHaveBeenCalledWith('通知綁定成功')
  })

  it('shows an error toast with the specific reason when the retry still fails', async () => {
    tgBound.value = false
    mockGetSessionStatus.mockResolvedValue(notifyFailedSession())
    mockRebindNotification.mockResolvedValue({
      notification_bind_status: 'bot_not_found',
      notification_bind_error: 'USERNAME_INVALID',
    })
    const wrapper = mountView()
    await flushPromises()
    mockGetSessionStatus.mockResolvedValue(
      notifyFailedSession({ notification_bind_status: 'bot_not_found', notification_bind_error: 'USERNAME_INVALID' }),
    )

    const buttons = wrapper.findAll('button')
    const retryBtn = buttons.find((b) => b.text().includes('重試通知綁定'))
    await retryBtn!.trigger('click')
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalledWith(expect.stringContaining('找不到該 bot'))
  })

  it('shows an error toast when the retry request itself throws', async () => {
    tgBound.value = false
    mockGetSessionStatus.mockResolvedValue(notifyFailedSession())
    mockRebindNotification.mockRejectedValue(new Error('network error'))
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const retryBtn = buttons.find((b) => b.text().includes('重試通知綁定'))
    await retryBtn!.trigger('click')
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalledWith(expect.stringContaining('network error'))
  })
})
