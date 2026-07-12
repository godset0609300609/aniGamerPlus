/**
 * Unit tests for the admin Telegram Bot 設定 section of SettingsView.vue.
 *
 * Covers:
 * - Admin user sees the "Telegram Bot 設定" section
 * - Non-admin (downloader) does NOT see the section
 * - Clicking "重新註冊 Webhook" calls the API + shows result
 * - Clicking "驗證 Bot Token" calls getBotMe + shows username
 * - Clicking "查看 Webhook 狀態" opens dialog with parsed fields
 * - Bot Token / Webhook Secret are write-only (draft input + status badge +
 *   dedicated save button) — bot_token/webhook_secret are NOT part of the
 *   WebSettings.telegram shape returned by GET /config.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createElementPlusStubs, elementPlusModuleMock } from '../helpers/elementPlusStubs'

// ---------------------------------------------------------------------------
// Auth stub — start as admin
// ---------------------------------------------------------------------------
const isAdminRef = ref(true)
const userRef = ref<{ id: string; username: string; role: string } | null>({
  id: 'admin-1',
  username: 'Admin',
  role: 'admin',
})

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ isAdmin: isAdminRef, user: userRef }),
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
const mockLoad = vi.fn()
const mockSave = vi.fn()

const BASE_SETTINGS = {
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
    bot_username: '',
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

const {
  mockSetTelegramBotToken,
  mockGetTelegramBotTokenStatus,
  mockSetTelegramWebhookSecret,
  mockGetTelegramWebhookSecretStatus,
} = vi.hoisted(() => ({
  mockSetTelegramBotToken: vi.fn(),
  mockGetTelegramBotTokenStatus: vi.fn(),
  mockSetTelegramWebhookSecret: vi.fn(),
  mockGetTelegramWebhookSecretStatus: vi.fn(),
}))

vi.mock('@/api/config', () => ({
  ConfigApi: vi.fn().mockImplementation(() => ({
    load: mockLoad,
    save: mockSave,
    setCookie: vi.fn().mockResolvedValue(undefined),
    getCookieStatus: vi.fn().mockResolvedValue({ configured: false }),
    setBilibiliCookie: vi.fn().mockResolvedValue({ status: 'ok' }),
    getBilibiliCookieStatus: vi.fn().mockResolvedValue({ configured: false }),
    setPutioToken: vi.fn().mockResolvedValue({ status: 'ok' }),
    getPutioTokenStatus: vi.fn().mockResolvedValue({ configured: false }),
    setTelegramBotToken: mockSetTelegramBotToken,
    getTelegramBotTokenStatus: mockGetTelegramBotTokenStatus,
    setTelegramWebhookSecret: mockSetTelegramWebhookSecret,
    getTelegramWebhookSecretStatus: mockGetTelegramWebhookSecretStatus,
  })),
  parseProxy: vi.fn().mockReturnValue({ protocol: 'HTTP', ip: '', port: '', user: '', password: '' }),
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
// useTelegramBinding stub
// ---------------------------------------------------------------------------
vi.mock('@/composables/useTelegramBinding', () => ({
  useTelegramBinding: () => ({
    bound: ref(false),
    notifyEnabled: ref(true),
    linkPending: ref(false),
    notConfigured: ref(false),
    loading: ref(false),
    error: ref<string | null>(null),
    countdownLabel: ref('9:45'),
    secondsRemaining: ref(585),
    loadStatus: vi.fn().mockResolvedValue(undefined),
    startLink: vi.fn().mockResolvedValue(undefined),
    unlink: vi.fn().mockResolvedValue(undefined),
    setNotifyEnabled: vi.fn().mockResolvedValue(undefined),
    dispose: vi.fn(),
  }),
}))

// ---------------------------------------------------------------------------
// telegram_admin API stub
// ---------------------------------------------------------------------------
const { mockRegisterWebhook, mockGetWebhookInfo, mockGetBotMe } = vi.hoisted(() => ({
  mockRegisterWebhook: vi.fn(),
  mockGetWebhookInfo: vi.fn(),
  mockGetBotMe: vi.fn(),
}))

vi.mock('@/api/telegram_admin', () => ({
  registerWebhook: mockRegisterWebhook,
  getWebhookInfo: mockGetWebhookInfo,
  getBotMe: mockGetBotMe,
  deleteWebhook: vi.fn(),
}))

// ---------------------------------------------------------------------------
// Element Plus mock
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
    ElMessageBox: {
      confirm: vi.fn().mockResolvedValue(undefined),
      alert: vi.fn(),
      prompt: vi.fn(),
    },
  }),
)

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
  localStorage.clear()
  // Every test in this file exercises the admin Telegram Bot 設定
  // subsection, which now lives under the Telegram tab (id "telegram")
  // rather than the default 一般 tab — deep-link straight into it via
  // ?tab= instead of clicking through nav.
  mockRoute.query = { tab: 'telegram' }
  isAdminRef.value = true
  userRef.value = { id: 'admin-1', username: 'Admin', role: 'admin' }
  mockLoad.mockResolvedValue(JSON.parse(JSON.stringify(BASE_SETTINGS)))
  mockSave.mockResolvedValue({ status: 'ok' })
  mockRegisterWebhook.mockResolvedValue({ ok: true, url: 'https://example.com/webhook' })
  mockGetWebhookInfo.mockResolvedValue({
    url: 'https://example.com/webhook',
    pending_update_count: 0,
    last_error_message: null,
  })
  mockGetBotMe.mockResolvedValue({ id: 123, username: 'testbot' })
  mockSetTelegramBotToken.mockResolvedValue({ status: 'ok' })
  mockGetTelegramBotTokenStatus.mockResolvedValue({ configured: false })
  mockSetTelegramWebhookSecret.mockResolvedValue({ status: 'ok' })
  mockGetTelegramWebhookSecretStatus.mockResolvedValue({ configured: false })
})

// ---------------------------------------------------------------------------
// Admin visibility
// ---------------------------------------------------------------------------

describe('SettingsView Admin Telegram Bot section — visibility', () => {
  it('admin sees the Telegram Bot 設定 section', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('Telegram Bot 設定')
  })

  it('non-admin (downloader) does NOT see the Telegram Bot 設定 section', async () => {
    isAdminRef.value = false
    userRef.value = { id: 'user-1', username: 'User', role: 'downloader' }

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).not.toContain('Telegram Bot 設定')
  })
})

// ---------------------------------------------------------------------------
// Register Webhook
// ---------------------------------------------------------------------------

describe('SettingsView Admin Telegram Bot section — register webhook', () => {
  beforeEach(() => {
    // Enable telegram so action buttons are not disabled
    mockLoad.mockResolvedValue(
      JSON.parse(JSON.stringify({ ...BASE_SETTINGS, telegram: { ...BASE_SETTINGS.telegram, enabled: true } })),
    )
  })

  it('clicking "重新註冊 Webhook" calls registerWebhook and shows success', async () => {
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const regBtn = buttons.find((b) => b.text().includes('重新註冊 Webhook'))
    expect(regBtn).toBeDefined()
    await regBtn!.trigger('click')
    await flushPromises()

    expect(mockRegisterWebhook).toHaveBeenCalledTimes(1)
    expect(mockElMessageSuccess).toHaveBeenCalledWith(
      expect.stringContaining('Webhook 已註冊'),
    )
  })

  it('registerWebhook failure shows error message', async () => {
    mockRegisterWebhook.mockRejectedValue(new Error('bot_token missing'))

    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const regBtn = buttons.find((b) => b.text().includes('重新註冊 Webhook'))
    await regBtn!.trigger('click')
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Verify Bot Token
// ---------------------------------------------------------------------------

describe('SettingsView Admin Telegram Bot section — verify bot token', () => {
  beforeEach(() => {
    // Enable telegram so the verify button is not disabled
    mockLoad.mockResolvedValue(
      JSON.parse(JSON.stringify({ ...BASE_SETTINGS, telegram: { ...BASE_SETTINGS.telegram, enabled: true } })),
    )
  })

  it('clicking "驗證 Bot Token" calls getBotMe and renders username', async () => {
    const wrapper = mountView()
    await flushPromises()

    // Auto-verify is only triggered on mount when a bot token is actually
    // configured server-side (mockGetTelegramBotTokenStatus defaults to
    // configured: false in this suite) — manually click the button instead.
    const buttons = wrapper.findAll('button')
    const verifyBtn = buttons.find((b) => b.text().includes('驗證 Bot Token'))
    expect(verifyBtn).toBeDefined()
    await verifyBtn!.trigger('click')
    await flushPromises()

    expect(mockGetBotMe).toHaveBeenCalledTimes(1)
    // Username should now appear
    expect(wrapper.text()).toContain('testbot')
  })

  it('auto-verifies on mount when telegram enabled and bot token is configured', async () => {
    mockLoad.mockResolvedValue(
      JSON.parse(JSON.stringify({ ...BASE_SETTINGS, telegram: { ...BASE_SETTINGS.telegram, enabled: true } })),
    )
    mockGetTelegramBotTokenStatus.mockResolvedValue({ configured: true })

    const wrapper = mountView()
    await flushPromises()

    expect(mockGetBotMe).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('testbot')
  })

  it('does NOT auto-verify on mount when no bot token is configured', async () => {
    mockLoad.mockResolvedValue(
      JSON.parse(JSON.stringify({ ...BASE_SETTINGS, telegram: { ...BASE_SETTINGS.telegram, enabled: true } })),
    )
    mockGetTelegramBotTokenStatus.mockResolvedValue({ configured: false })

    mountView()
    await flushPromises()

    expect(mockGetBotMe).not.toHaveBeenCalled()
  })

  it('getBotMe failure shows error', async () => {
    mockGetBotMe.mockRejectedValue(new Error('invalid token'))

    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const verifyBtn = buttons.find((b) => b.text().includes('驗證 Bot Token'))
    await verifyBtn!.trigger('click')
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Webhook Status dialog
// ---------------------------------------------------------------------------

describe('SettingsView Admin Telegram Bot section — webhook status dialog', () => {
  beforeEach(() => {
    // Enable telegram so the status button is not disabled
    mockLoad.mockResolvedValue(
      JSON.parse(JSON.stringify({ ...BASE_SETTINGS, telegram: { ...BASE_SETTINGS.telegram, enabled: true } })),
    )
  })

  it('clicking "查看 Webhook 狀態" calls getWebhookInfo and opens dialog', async () => {
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const statusBtn = buttons.find((b) => b.text().includes('查看 Webhook 狀態'))
    expect(statusBtn).toBeDefined()
    await statusBtn!.trigger('click')
    await flushPromises()

    expect(mockGetWebhookInfo).toHaveBeenCalledTimes(1)
    // Dialog content should contain the URL
    expect(wrapper.text()).toContain('https://example.com/webhook')
  })

  it('getWebhookInfo failure shows error', async () => {
    mockGetWebhookInfo.mockRejectedValue(new Error('bot not configured'))

    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const statusBtn = buttons.find((b) => b.text().includes('查看 Webhook 狀態'))
    await statusBtn!.trigger('click')
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Dirty detection (telegram fields)
// ---------------------------------------------------------------------------

describe('SettingsView Admin Telegram Bot section — dirty detection', () => {
  it('dirty is false immediately after load', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { dirty: boolean }
    expect(vm.dirty).toBe(false)
  })

  it('dirty becomes true after changing enabled', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      dirty: boolean
      settings: { telegram: { enabled: boolean } } | null
    }
    if (vm.settings) vm.settings.telegram.enabled = true
    await wrapper.vm.$nextTick()
    expect(vm.dirty).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// GET /config never exposes bot_token / webhook_secret (fix #1 CRITICAL)
// ---------------------------------------------------------------------------

describe('SettingsView Admin Telegram Bot section — secrets are write-only', () => {
  it('settings.telegram loaded from GET /config has no bot_token / webhook_secret keys', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { settings: { telegram: Record<string, unknown> } | null }
    expect(vm.settings?.telegram).toBeDefined()
    expect(vm.settings?.telegram).not.toHaveProperty('bot_token')
    expect(vm.settings?.telegram).not.toHaveProperty('webhook_secret')
  })

  it('save payload never contains a bot_token / webhook_secret key', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { save: () => Promise<void> }
    await vm.save()
    await flushPromises()

    expect(mockSave).toHaveBeenCalledTimes(1)
    const savedTelegram = mockSave.mock.calls[0][0].telegram as Record<string, unknown>
    expect(savedTelegram).not.toHaveProperty('bot_token')
    expect(savedTelegram).not.toHaveProperty('webhook_secret')
  })
})

// ---------------------------------------------------------------------------
// Bot Token — write-only draft + status badge
// ---------------------------------------------------------------------------

describe('SettingsView Admin Telegram Bot section — bot token write-only field', () => {
  it('shows "尚未設定" when no bot token is configured', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('尚未設定')
  })

  it('shows "目前已設定" when a bot token is configured', async () => {
    mockGetTelegramBotTokenStatus.mockResolvedValue({ configured: true })

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('目前已設定')
  })

  it('clicking 儲存 calls setTelegramBotToken with the draft value and clears the draft', async () => {
    const wrapper = mountView()
    await flushPromises()

    const input = wrapper
      .findAll('input.el-input')
      .find((i) => i.attributes('placeholder') === '輸入新的 Bot Token')
    expect(input).toBeDefined()
    await input!.setValue('123456:NEW-TOKEN')

    const row = wrapper
      .findAll('.cookie-row')
      .find((r) => r.find('input.el-input').attributes('placeholder') === '輸入新的 Bot Token')
    expect(row).toBeDefined()
    const saveBtn = row!.findAll('button').find((b) => b.text().includes('儲存'))
    expect(saveBtn).toBeDefined()
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(mockSetTelegramBotToken).toHaveBeenCalledWith('123456:NEW-TOKEN')
    expect(mockElMessageSuccess).toHaveBeenCalledWith('Bot Token 已更新')
    expect((input!.element as HTMLInputElement).value).toBe('')
  })

  it('shows an error message when setTelegramBotToken rejects', async () => {
    mockSetTelegramBotToken.mockRejectedValue(new Error('invalid token'))

    const wrapper = mountView()
    await flushPromises()

    const input = wrapper
      .findAll('input.el-input')
      .find((i) => i.attributes('placeholder') === '輸入新的 Bot Token')!
    await input.setValue('bad-token')

    const row = wrapper
      .findAll('.cookie-row')
      .find((r) => r.find('input.el-input').attributes('placeholder') === '輸入新的 Bot Token')!
    const saveBtn = row.findAll('button').find((b) => b.text().includes('儲存'))!
    await saveBtn.trigger('click')
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Bot Username — plain config field (round-trips through GET/PUT /config,
// unlike bot_token/webhook_secret which are write-only)
// ---------------------------------------------------------------------------

describe('SettingsView Admin Telegram Bot section — bot username field', () => {
  const BOT_USERNAME_PLACEHOLDER = '@YourBotUsername'

  it('renders the Bot Username input and binds it to settings.telegram.bot_username', async () => {
    mockLoad.mockResolvedValue(
      JSON.parse(
        JSON.stringify({
          ...BASE_SETTINGS,
          telegram: { ...BASE_SETTINGS.telegram, bot_username: 'ExistingBot' },
        }),
      ),
    )

    const wrapper = mountView()
    await flushPromises()

    const input = wrapper
      .findAll('input.el-input')
      .find((i) => i.attributes('placeholder') === BOT_USERNAME_PLACEHOLDER)
    expect(input).toBeDefined()
    expect((input!.element as HTMLInputElement).value).toBe('ExistingBot')

    const vm = wrapper.vm as unknown as { settings: { telegram: { bot_username: string } } | null }
    expect(vm.settings?.telegram.bot_username).toBe('ExistingBot')
  })

  it('submits telegram.bot_username in the PUT payload when config is saved', async () => {
    const wrapper = mountView()
    await flushPromises()

    const input = wrapper
      .findAll('input.el-input')
      .find((i) => i.attributes('placeholder') === BOT_USERNAME_PLACEHOLDER)
    expect(input).toBeDefined()
    await input!.setValue('MyNotifyBot')

    const vm = wrapper.vm as unknown as { save: () => Promise<void> }
    await vm.save()
    await flushPromises()

    expect(mockSave).toHaveBeenCalledTimes(1)
    const savedTelegram = mockSave.mock.calls[0][0].telegram as Record<string, unknown>
    expect(savedTelegram.bot_username).toBe('MyNotifyBot')
  })

  it('clearing the field leaves bot_username as an empty string (the unset default), not null', async () => {
    mockLoad.mockResolvedValue(
      JSON.parse(
        JSON.stringify({
          ...BASE_SETTINGS,
          telegram: { ...BASE_SETTINGS.telegram, bot_username: 'ExistingBot' },
        }),
      ),
    )

    const wrapper = mountView()
    await flushPromises()

    const input = wrapper
      .findAll('input.el-input')
      .find((i) => i.attributes('placeholder') === BOT_USERNAME_PLACEHOLDER)
    expect(input).toBeDefined()
    await input!.setValue('')

    const vm = wrapper.vm as unknown as {
      save: () => Promise<void>
      settings: { telegram: { bot_username: string } } | null
    }
    expect(vm.settings?.telegram.bot_username).toBe('')

    await vm.save()
    await flushPromises()

    const savedTelegram = mockSave.mock.calls[0][0].telegram as Record<string, unknown>
    expect(savedTelegram.bot_username).toBe('')
  })
})

// ---------------------------------------------------------------------------
// Bot Username — input normalization on blur + format validation feedback
// ---------------------------------------------------------------------------

describe('SettingsView Admin Telegram Bot section — bot username normalization', () => {
  const BOT_USERNAME_PLACEHOLDER = '@YourBotUsername'

  function findBotUsernameInput(wrapper: ReturnType<typeof mountView>) {
    return wrapper.findAll('input.el-input').find((i) => i.attributes('placeholder') === BOT_USERNAME_PLACEHOLDER)!
  }

  it('prepends @ on blur when the typed value is missing it', async () => {
    const wrapper = mountView()
    await flushPromises()

    const input = findBotUsernameInput(wrapper)
    await input.setValue('aniGamerPlusBot')
    await input.trigger('blur')
    await flushPromises()

    expect((input.element as HTMLInputElement).value).toBe('@aniGamerPlusBot')
  })

  it('strips a pasted https://t.me/ prefix and converts it to @ on blur', async () => {
    const wrapper = mountView()
    await flushPromises()

    const input = findBotUsernameInput(wrapper)
    await input.setValue('https://t.me/aniGamerPlusBot')
    await input.trigger('blur')
    await flushPromises()

    expect((input.element as HTMLInputElement).value).toBe('@aniGamerPlusBot')
  })

  it('trims surrounding whitespace on blur', async () => {
    const wrapper = mountView()
    await flushPromises()

    const input = findBotUsernameInput(wrapper)
    await input.setValue('  aniGamerPlusBot  ')
    await input.trigger('blur')
    await flushPromises()

    expect((input.element as HTMLInputElement).value).toBe('@aniGamerPlusBot')
  })

  it('leaves an empty value empty on blur (does not turn it into a lone @)', async () => {
    const wrapper = mountView()
    await flushPromises()

    const input = findBotUsernameInput(wrapper)
    await input.setValue('')
    await input.trigger('blur')
    await flushPromises()

    expect((input.element as HTMLInputElement).value).toBe('')
  })

  it('reports no validation error for a well-formed handle', async () => {
    const wrapper = mountView()
    await flushPromises()

    const input = findBotUsernameInput(wrapper)
    await input.setValue('@aniGamerPlusBot')
    await flushPromises()

    const vm = wrapper.vm as unknown as { botUsernameError: string }
    expect(vm.botUsernameError).toBe('')
  })

  it('reports no validation error for an empty value (field is optional)', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { botUsernameError: string }
    expect(vm.botUsernameError).toBe('')
  })

  it('reports a validation error for a malformed handle', async () => {
    const wrapper = mountView()
    await flushPromises()

    const input = findBotUsernameInput(wrapper)
    await input.setValue('ab')
    await flushPromises()

    const vm = wrapper.vm as unknown as { botUsernameError: string }
    expect(vm.botUsernameError).toContain('格式錯誤')
  })
})

// ---------------------------------------------------------------------------
// Bot Username — proactive "尚未設定" warning banner
// ---------------------------------------------------------------------------

describe('SettingsView Admin Telegram Bot section — bot username warning banner', () => {
  const WARNING_TITLE = 'Bot Username 尚未設定'
  const DISMISS_KEY = 'dismissed-bot-username-warning'

  it('shows the warning when telegram is enabled and bot_username is empty', async () => {
    mockLoad.mockResolvedValue(
      JSON.parse(
        JSON.stringify({
          ...BASE_SETTINGS,
          telegram: { ...BASE_SETTINGS.telegram, enabled: true, bot_username: '' },
        }),
      ),
    )
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { showBotUsernameWarning: boolean }
    expect(vm.showBotUsernameWarning).toBe(true)
  })

  it('hides the warning once bot_username is set', async () => {
    mockLoad.mockResolvedValue(
      JSON.parse(
        JSON.stringify({
          ...BASE_SETTINGS,
          telegram: { ...BASE_SETTINGS.telegram, enabled: true, bot_username: '@aniGamerPlusBot' },
        }),
      ),
    )
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { showBotUsernameWarning: boolean }
    expect(vm.showBotUsernameWarning).toBe(false)
  })

  it('hides the warning when telegram bot is disabled, even with bot_username empty', async () => {
    mockLoad.mockResolvedValue(
      JSON.parse(
        JSON.stringify({
          ...BASE_SETTINGS,
          telegram: { ...BASE_SETTINGS.telegram, enabled: false, bot_username: '' },
        }),
      ),
    )
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { showBotUsernameWarning: boolean }
    expect(vm.showBotUsernameWarning).toBe(false)
  })

  it('renders the warning title text when shown', async () => {
    mockLoad.mockResolvedValue(
      JSON.parse(
        JSON.stringify({
          ...BASE_SETTINGS,
          telegram: { ...BASE_SETTINGS.telegram, enabled: true, bot_username: '' },
        }),
      ),
    )
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.html()).toContain(WARNING_TITLE)
  })

  it('dismissing the warning persists to localStorage and hides it', async () => {
    mockLoad.mockResolvedValue(
      JSON.parse(
        JSON.stringify({
          ...BASE_SETTINGS,
          telegram: { ...BASE_SETTINGS.telegram, enabled: true, bot_username: '' },
        }),
      ),
    )
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { dismissBotUsernameWarning: () => void; showBotUsernameWarning: boolean }
    expect(vm.showBotUsernameWarning).toBe(true)
    vm.dismissBotUsernameWarning()
    await flushPromises()

    expect(vm.showBotUsernameWarning).toBe(false)
    expect(localStorage.getItem(DISMISS_KEY)).toBe('1')
  })

  it('does not show the warning again in a fresh mount after dismissal was persisted', async () => {
    localStorage.setItem(DISMISS_KEY, '1')
    mockLoad.mockResolvedValue(
      JSON.parse(
        JSON.stringify({
          ...BASE_SETTINGS,
          telegram: { ...BASE_SETTINGS.telegram, enabled: true, bot_username: '' },
        }),
      ),
    )
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { showBotUsernameWarning: boolean }
    expect(vm.showBotUsernameWarning).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Webhook Secret — write-only draft + status badge + generate button
// ---------------------------------------------------------------------------

describe('SettingsView Admin Telegram Bot section — webhook secret write-only field', () => {
  it('shows "尚未設定" when no webhook secret is configured', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('尚未設定')
  })

  it('shows "目前已設定" when a webhook secret is configured', async () => {
    mockGetTelegramWebhookSecretStatus.mockResolvedValue({ configured: true })

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('目前已設定')
  })

  it('clicking "產生" fills the draft input with a random hex string', async () => {
    const wrapper = mountView()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const genBtn = buttons.find((b) => b.text().includes('產生'))
    expect(genBtn).toBeDefined()
    await genBtn!.trigger('click')
    await flushPromises()

    const input = wrapper
      .findAll('input.el-input')
      .find((i) => i.attributes('placeholder') === '輸入新的 Webhook 密鑰')!
    expect((input.element as HTMLInputElement).value).toMatch(/^[0-9a-f]{64}$/)
  })

  it('clicking 儲存 calls setTelegramWebhookSecret with the draft value and clears the draft', async () => {
    const wrapper = mountView()
    await flushPromises()

    const input = wrapper
      .findAll('input.el-input')
      .find((i) => i.attributes('placeholder') === '輸入新的 Webhook 密鑰')
    expect(input).toBeDefined()
    await input!.setValue('deadbeef')

    const row = wrapper
      .findAll('.cookie-row')
      .find((r) => r.find('input.el-input').attributes('placeholder') === '輸入新的 Webhook 密鑰')
    expect(row).toBeDefined()
    const saveBtn = row!.findAll('button').find((b) => b.text().includes('儲存'))
    expect(saveBtn).toBeDefined()
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(mockSetTelegramWebhookSecret).toHaveBeenCalledWith('deadbeef')
    expect(mockElMessageSuccess).toHaveBeenCalledWith('Webhook Secret 已更新')
    expect((input!.element as HTMLInputElement).value).toBe('')
  })

  it('shows an error message when setTelegramWebhookSecret rejects', async () => {
    mockSetTelegramWebhookSecret.mockRejectedValue(new Error('too short'))

    const wrapper = mountView()
    await flushPromises()

    const input = wrapper
      .findAll('input.el-input')
      .find((i) => i.attributes('placeholder') === '輸入新的 Webhook 密鑰')!
    await input.setValue('x')

    const row = wrapper
      .findAll('.cookie-row')
      .find((r) => r.find('input.el-input').attributes('placeholder') === '輸入新的 Webhook 密鑰')!
    const saveBtn = row.findAll('button').find((b) => b.text().includes('儲存'))!
    await saveBtn.trigger('click')
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalled()
  })
})
