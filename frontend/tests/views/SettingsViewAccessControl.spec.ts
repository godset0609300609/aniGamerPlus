/**
 * Tests for Issues 1–4 in SettingsView.vue:
 *
 * Issue 1  — Non-admin sees only Telegram binding; admin sees all.
 * Issue 2  — Notify-on checkboxes show 中文 labels.
 * Issue 3  — Admin action buttons disabled when telegram.enabled=false; reactive to form state.
 * Issue 4  — Non-admin with telegram.enabled=false sees muted notice; bound user sees warning.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createElementPlusStubs, elementPlusModuleMock } from '../helpers/elementPlusStubs'

// ---------------------------------------------------------------------------
// Auth stub — controllable isAdmin ref.
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
// useTelegramBinding stub — controllable state.
// ---------------------------------------------------------------------------
const tgBound = ref(false)
const tgNotifyEnabled = ref(true)
const tgLinkPending = ref(false)
const tgNotConfigured = ref(false)
const tgLoading = ref(false)
const tgError = ref<string | null>(null)
const tgCountdownLabel = ref('9:45')

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
    loadStatus: vi.fn().mockResolvedValue(undefined),
    startLink: vi.fn().mockResolvedValue(undefined),
    unlink: vi.fn().mockResolvedValue(undefined),
    setNotifyEnabled: vi.fn().mockResolvedValue(undefined),
    dispose: vi.fn(),
  }),
}))

// ---------------------------------------------------------------------------
// ConfigApi stub — load returns configurable settings.
// ---------------------------------------------------------------------------
const mockLoad = vi.fn()

vi.mock('@/api/config', () => ({
  ConfigApi: vi.fn().mockImplementation(() => ({
    load: mockLoad,
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
// telegram_admin API stub.
// ---------------------------------------------------------------------------
vi.mock('@/api/telegram_admin', () => ({
  registerWebhook: vi.fn().mockResolvedValue({ ok: true, url: 'https://example.com/webhook' }),
  getWebhookInfo: vi.fn().mockResolvedValue({ url: null, pending_update_count: 0 }),
  getBotMe: vi.fn().mockResolvedValue({ id: 1, username: 'testbot' }),
  deleteWebhook: vi.fn(),
}))

// ---------------------------------------------------------------------------
// Element Plus mock.
// ---------------------------------------------------------------------------
vi.mock('element-plus', () =>
  elementPlusModuleMock({
    ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
    ElMessageBox: { confirm: vi.fn().mockResolvedValue(undefined), alert: vi.fn(), prompt: vi.fn() },
  }),
)

// Import component AFTER mocks are set up.
import SettingsView from '@/views/SettingsView.vue'

const stubs = createElementPlusStubs()

function makeSettings(telegramOverrides: Record<string, unknown> = {}) {
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
      ...telegramOverrides,
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

/** Activates a tab by clicking its <el-tabs> nav item. */
async function switchTab(wrapper: ReturnType<typeof mountView>, tabId: string) {
  const nav = wrapper.find(`.el-tabs__item[data-name="${tabId}"]`)
  await nav.trigger('click')
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
  mockRoute.query = {}
  isAdminRef.value = true
  tgBound.value = false
  tgNotifyEnabled.value = true
  tgLinkPending.value = false
  tgNotConfigured.value = false
  tgLoading.value = false
  tgError.value = null
  mockLoad.mockResolvedValue(makeSettings())
})

// ===========================================================================
// Issue 1 — Access control: admin vs non-admin visibility
// ===========================================================================

describe('Issue 1 — Admin sees full settings', () => {
  it('admin sees 路徑設定 section', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('路徑設定')
  })

  it('admin sees 下載設定 section', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('下載設定')
  })

  it('admin sees 代理設定 section', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('代理設定')
  })

  it('admin sees Cookie section', async () => {
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'source')
    expect(wrapper.text()).toContain('Cookie')
  })

  it('admin sees 其他 section', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('其他')
  })

  it('admin sees DirtyFab', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.dirty-fab-stub').exists()).toBe(true)
  })
})

describe('Issue 1 — Non-admin (downloader) sees only Telegram binding section', () => {
  beforeEach(() => {
    isAdminRef.value = false
  })

  it('non-admin sees the merged Telegram section', async () => {
    const wrapper = mountView()
    await flushPromises()
    const headings = wrapper.findAll('h2').map((h) => h.text().trim())
    expect(headings).toContain('Telegram')
  })

  it('non-admin does NOT see 路徑設定', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).not.toContain('路徑設定')
  })

  it('non-admin does NOT see 下載設定', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).not.toContain('下載設定')
  })

  it('non-admin does NOT see 代理設定', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).not.toContain('代理設定')
  })

  it('non-admin does NOT see Cookie section', async () => {
    const wrapper = mountView()
    await flushPromises()
    // Only the telegram binding section is present; "Cookie 只有管理員" hint is hidden
    const inputs = wrapper.findAll('input.el-input')
    const cookieInput = inputs.find(
      (i) => i.attributes('placeholder') === '貼上完整 cookie 字串',
    )
    expect(cookieInput).toBeUndefined()
  })

  it('non-admin does NOT see 其他 section', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).not.toContain('每次檢查讀取追番清單')
  })

  it('non-admin does NOT see DirtyFab', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.dirty-fab-stub').exists()).toBe(false)
  })

  it('non-admin does NOT see Telegram Bot 設定 section', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).not.toContain('Telegram Bot 設定')
  })
})

// ===========================================================================
// Issue 2 — i18n labels for notify-on checkboxes and allow-localhost
// ===========================================================================

describe('Issue 2 — Chinese labels in admin Telegram Bot section', () => {
  beforeEach(() => {
    mockLoad.mockResolvedValue(makeSettings({ enabled: true }))
  })

  it('renders 下載完成 label for completed checkbox', async () => {
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'telegram')
    expect(wrapper.text()).toContain('下載完成')
  })

  it('renders 下載失敗 label for failed checkbox', async () => {
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'telegram')
    expect(wrapper.text()).toContain('下載失敗')
  })

  it('renders 下載取消 label for cancelled checkbox', async () => {
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'telegram')
    expect(wrapper.text()).toContain('下載取消')
  })
})

// ===========================================================================
// Issue 3 — Admin action buttons gated on telegram.enabled
// ===========================================================================

describe('Issue 3 — Admin action buttons disabled when telegram.enabled=false', () => {
  it('重新註冊 Webhook button is disabled when enabled=false', async () => {
    mockLoad.mockResolvedValue(makeSettings({ enabled: false }))
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'telegram')

    const buttons = wrapper.findAll('button')
    const btn = buttons.find((b) => b.text().includes('重新註冊 Webhook'))
    expect(btn).toBeDefined()
    expect(btn!.attributes('disabled')).toBeDefined()
  })

  it('查看 Webhook 狀態 button is disabled when enabled=false', async () => {
    mockLoad.mockResolvedValue(makeSettings({ enabled: false }))
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'telegram')

    const buttons = wrapper.findAll('button')
    const btn = buttons.find((b) => b.text().includes('查看 Webhook 狀態'))
    expect(btn).toBeDefined()
    expect(btn!.attributes('disabled')).toBeDefined()
  })

  it('驗證 Bot Token button is disabled when enabled=false', async () => {
    mockLoad.mockResolvedValue(makeSettings({ enabled: false }))
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'telegram')

    const buttons = wrapper.findAll('button')
    const btn = buttons.find((b) => b.text().includes('驗證 Bot Token'))
    expect(btn).toBeDefined()
    expect(btn!.attributes('disabled')).toBeDefined()
  })

  it('action buttons are enabled when telegram.enabled=true', async () => {
    mockLoad.mockResolvedValue(makeSettings({ enabled: true }))
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'telegram')

    const buttons = wrapper.findAll('button')
    const webhookBtn = buttons.find((b) => b.text().includes('重新註冊 Webhook'))
    const statusBtn = buttons.find((b) => b.text().includes('查看 Webhook 狀態'))
    const verifyBtn = buttons.find((b) => b.text().includes('驗證 Bot Token'))

    expect(webhookBtn!.attributes('disabled')).toBeUndefined()
    expect(statusBtn!.attributes('disabled')).toBeUndefined()
    expect(verifyBtn!.attributes('disabled')).toBeUndefined()
  })

  it('flipping enabled toggle in UI immediately enables buttons (no save required)', async () => {
    mockLoad.mockResolvedValue(makeSettings({ enabled: false }))
    const wrapper = mountView()
    await flushPromises()
    await switchTab(wrapper, 'telegram')

    // Mutate settings directly via vm — simulates toggling the switch
    const vm = wrapper.vm as unknown as {
      settings: { telegram: { enabled: boolean } } | null
    }
    if (vm.settings) vm.settings.telegram.enabled = true
    await wrapper.vm.$nextTick()

    const buttons = wrapper.findAll('button')
    const webhookBtn = buttons.find((b) => b.text().includes('重新註冊 Webhook'))
    expect(webhookBtn!.attributes('disabled')).toBeUndefined()
  })
})

// ===========================================================================
// Issue 4 — Non-admin binding UI gated on telegram.enabled
// ===========================================================================

describe('Issue 4 — Non-admin sees muted notice when telegram.enabled=false and not bound', () => {
  beforeEach(() => {
    isAdminRef.value = false
    tgBound.value = false
    tgNotConfigured.value = false
    mockLoad.mockResolvedValue(makeSettings({ enabled: false }))
  })

  it('shows muted notice "系統管理員尚未啟用 Telegram 通知功能" when disabled and not bound', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('系統管理員尚未啟用 Telegram 通知功能')
  })

  it('does NOT show 綁定 Telegram button when disabled and not bound', async () => {
    const wrapper = mountView()
    await flushPromises()
    const buttons = wrapper.findAll('button')
    const bindBtn = buttons.find((b) => b.text().includes('綁定 Telegram'))
    expect(bindBtn).toBeUndefined()
  })
})

describe('Issue 4 — Non-admin already bound with telegram.enabled=false sees warning', () => {
  beforeEach(() => {
    isAdminRef.value = false
    tgBound.value = true
    tgNotConfigured.value = false
    mockLoad.mockResolvedValue(makeSettings({ enabled: false }))
  })

  it('shows 已綁定 status', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('已綁定')
  })

  it('shows warning banner about disabled notifications', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('系統 Telegram 通知目前停用中')
  })
})

describe('Issue 4 — Non-admin with telegram.enabled=true sees bind button', () => {
  beforeEach(() => {
    isAdminRef.value = false
    tgBound.value = false
    tgNotConfigured.value = false
    mockLoad.mockResolvedValue(makeSettings({ enabled: true }))
  })

  it('shows 綁定 Telegram button when enabled=true and not bound', async () => {
    const wrapper = mountView()
    await flushPromises()
    const buttons = wrapper.findAll('button')
    const bindBtn = buttons.find((b) => b.text().includes('綁定 Telegram'))
    expect(bindBtn).toBeDefined()
  })
})
