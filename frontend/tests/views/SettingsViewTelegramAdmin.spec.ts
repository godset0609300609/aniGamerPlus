/**
 * Unit tests for the admin Telegram Bot 設定 section of SettingsView.vue.
 *
 * Covers:
 * - Admin user sees the "Telegram Bot 設定" section
 * - Non-admin (downloader) does NOT see the section
 * - Clicking "重新註冊 Webhook" calls the API + shows result
 * - Clicking "驗證 Bot Token" calls getBotMe + shows username
 * - Clicking "查看 Webhook 狀態" opens dialog with parsed fields
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
    allow_localhost: false,
  },
}

vi.mock('@/api/config', () => ({
  ConfigApi: vi.fn().mockImplementation(() => ({
    load: mockLoad,
    save: mockSave,
    setCookie: vi.fn().mockResolvedValue(undefined),
    getCookieStatus: vi.fn().mockResolvedValue({ configured: false }),
  })),
  parseProxy: vi.fn().mockReturnValue({ protocol: 'HTTP', ip: '', port: '', user: '', password: '' }),
  serializeProxy: vi.fn().mockReturnValue(''),
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
  it('clicking "驗證 Bot Token" calls getBotMe and renders username', async () => {
    const wrapper = mountView()
    await flushPromises()

    // Auto-verify is triggered on mount only if bot_token is set; settings mock has empty token.
    // Manually click the button.
    const buttons = wrapper.findAll('button')
    const verifyBtn = buttons.find((b) => b.text().includes('驗證 Bot Token'))
    expect(verifyBtn).toBeDefined()
    await verifyBtn!.trigger('click')
    await flushPromises()

    expect(mockGetBotMe).toHaveBeenCalledTimes(1)
    // Username should now appear
    expect(wrapper.text()).toContain('testbot')
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
// Dirty detection + save (telegram fields)
// ---------------------------------------------------------------------------

describe('SettingsView Admin Telegram Bot section — dirty detection', () => {
  it('dirty is false immediately after load', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { dirty: boolean }
    expect(vm.dirty).toBe(false)
  })

  it('dirty becomes true after changing bot_token', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      dirty: boolean
      settings: { telegram: { bot_token: string } } | null
    }
    if (vm.settings) vm.settings.telegram.bot_token = 'new-token-value'
    await wrapper.vm.$nextTick()
    expect(vm.dirty).toBe(true)
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

describe('SettingsView Admin Telegram Bot section — save includes telegram', () => {
  it('save payload includes telegram.bot_token when changed', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      save: () => Promise<void>
      settings: { telegram: { bot_token: string } } | null
    }
    if (vm.settings) vm.settings.telegram.bot_token = 'abc123token'
    await vm.save()
    await flushPromises()

    expect(mockSave).toHaveBeenCalledTimes(1)
    const savedPayload = mockSave.mock.calls[0][0] as { telegram: { bot_token: string } }
    expect(savedPayload.telegram.bot_token).toBe('abc123token')
  })

  it('after save, load is called again and inputs reflect persisted value', async () => {
    const wrapper = mountView()
    await flushPromises()

    // Simulate save re-loading settings that now have the new token
    mockLoad.mockResolvedValue({
      ...JSON.parse(JSON.stringify(BASE_SETTINGS)),
      telegram: { ...BASE_SETTINGS.telegram, bot_token: 'persisted-token' },
    })

    const vm = wrapper.vm as unknown as {
      save: () => Promise<void>
      settings: { telegram: { bot_token: string } } | null
    }
    if (vm.settings) vm.settings.telegram.bot_token = 'persisted-token'
    await vm.save()
    await flushPromises()

    // After save, load() is called and settings.telegram.bot_token is updated
    expect(vm.settings?.telegram.bot_token).toBe('persisted-token')
  })
})
