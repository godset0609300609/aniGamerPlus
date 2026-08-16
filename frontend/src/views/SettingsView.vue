<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ConfigApi, parseProxy, serializeProxy } from '@/api/config'
import { TgApi } from '@/api/tg'
import type { ProxyParts, TelegramWebhookInfo, TgNotificationBindStatus, TgSession, WebSettings } from '@/types'
import { useAuthStore } from '@/stores/auth'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useTelegramBinding } from '@/composables/useTelegramBinding'
import { getBotMe, getWebhookInfo, registerWebhook } from '@/api/telegram_admin'
import { isValidBotUsername, normalizeBotUsername } from '@/utils/botUsername'
import DirtyFab from '@/components/DirtyFab.vue'
import TgBindDialog from '@/components/tg/TgBindDialog.vue'

const api = new ConfigApi()
const { isAdmin } = useAuthStore()
const { isMobile } = useBreakpoint()

// ---------------------------------------------------------------------------
// Tabs — groups the page's sections under <el-tabs>. Admin-only tabs are
// dropped from the list entirely for non-admin users (rather than rendered
// empty), mirroring the previous flat-page behaviour where non-admin users
// only ever saw the Telegram section.
// ---------------------------------------------------------------------------

interface SettingsTabDef {
  id: string
  label: string
  adminOnly: boolean
}

// Order matters — it defines "later" vs "earlier" for the slide direction,
// and is the fallback order used to pick the default tab.
const TAB_DEFS: SettingsTabDef[] = [
  { id: 'general', label: '一般', adminOnly: true },
  { id: 'source', label: '來源', adminOnly: true },
  { id: 'bt', label: 'BT 下載', adminOnly: true },
  { id: 'telegram', label: 'Telegram', adminOnly: false },
]
const TAB_ORDER = TAB_DEFS.map((t) => t.id)

const visibleTabs = computed(() => TAB_DEFS.filter((t) => !t.adminOnly || isAdmin.value))

const route = useRoute()
const router = useRouter()

function resolveTabFromQuery(): string {
  const requested = typeof route.query.tab === 'string' ? route.query.tab : ''
  const ids = visibleTabs.value.map((t) => t.id)
  if (requested && ids.includes(requested)) return requested
  return ids[0] ?? 'telegram'
}

const activeTab = ref(resolveTabFromQuery())

// Direction of the panel transition: 'left' when moving to a later tab,
// 'right' when moving to an earlier one — mirrors BtView.vue's tab-switch
// slide animation.
const slideDir = ref<'left' | 'right'>('left')

watch(activeTab, (next, prev) => {
  const nextIndex = TAB_ORDER.indexOf(next)
  const prevIndex = TAB_ORDER.indexOf(prev)
  if (nextIndex !== -1 && prevIndex !== -1) {
    slideDir.value = nextIndex > prevIndex ? 'left' : 'right'
  }
})

// If admin status changes after mount (e.g. session downgraded) and the
// active tab is no longer visible, fall back to the first visible tab.
watch(isAdmin, () => {
  if (!visibleTabs.value.some((t) => t.id === activeTab.value)) {
    activeTab.value = visibleTabs.value[0]?.id ?? 'telegram'
  }
})

watch(
  () => route.query.tab,
  (tab) => {
    if (typeof tab === 'string' && tab && tab !== activeTab.value && visibleTabs.value.some((t) => t.id === tab)) {
      activeTab.value = tab
    }
  },
)

watch(activeTab, (tab) => {
  if (route.query.tab === tab) return
  router.replace({ path: '/settings', query: { tab } })
})

const settings = ref<WebSettings | null>(null)
const proxyParts = reactive<ProxyParts>({
  protocol: 'HTTP',
  ip: '',
  port: '',
  user: '',
  password: '',
})
const saving = ref(false)
const snapshot = ref<string>('')

// ---------------------------------------------------------------------------
// Cookie (write-only)
// ---------------------------------------------------------------------------
const cookieDraft = ref<string>('')
const cookieStatus = ref<{ configured: boolean }>({ configured: false })
const savingCookie = ref(false)

async function submitCookie(): Promise<void> {
  savingCookie.value = true
  try {
    await api.setCookie(cookieDraft.value)
    ElMessage.success('Cookie 已更新')
    cookieDraft.value = ''
    cookieStatus.value = { configured: true }
  } catch (e) {
    ElMessage.error(`儲存失敗: ${(e as Error).message}`)
  } finally {
    savingCookie.value = false
  }
}

// ---------------------------------------------------------------------------
// Bilibili Cookie (write-only)
// ---------------------------------------------------------------------------
const bilibiliCookieDraft = ref<string>('')
const bilibiliCookieStatus = ref<{ configured: boolean }>({ configured: false })
const savingBilibiliCookie = ref(false)

async function submitBilibiliCookie(): Promise<void> {
  savingBilibiliCookie.value = true
  try {
    await api.setBilibiliCookie(bilibiliCookieDraft.value)
    ElMessage.success('Bilibili Cookie 已更新')
    bilibiliCookieDraft.value = ''
    bilibiliCookieStatus.value = { configured: true }
  } catch (e) {
    ElMessage.error(`儲存失敗: ${(e as Error).message}`)
  } finally {
    savingBilibiliCookie.value = false
  }
}

// ---------------------------------------------------------------------------
// Put.io token (write-only)
// ---------------------------------------------------------------------------
const putioTokenDraft = ref<string>('')
const putioTokenStatus = ref<{ configured: boolean }>({ configured: false })
const savingPutioToken = ref(false)

async function submitPutioToken(): Promise<void> {
  savingPutioToken.value = true
  try {
    await api.setPutioToken(putioTokenDraft.value)
    ElMessage.success('Put.io token 已更新')
    putioTokenDraft.value = ''
    putioTokenStatus.value = { configured: true }
  } catch (e) {
    ElMessage.error(`儲存失敗: ${(e as Error).message}`)
  } finally {
    savingPutioToken.value = false
  }
}

// ---------------------------------------------------------------------------
// Telegram bot token (write-only)
// ---------------------------------------------------------------------------
const tgBotTokenDraft = ref<string>('')
const tgBotTokenStatus = ref<{ configured: boolean }>({ configured: false })
const savingTgBotToken = ref(false)

async function submitTelegramBotToken(): Promise<void> {
  savingTgBotToken.value = true
  try {
    await api.setTelegramBotToken(tgBotTokenDraft.value)
    ElMessage.success('Bot Token 已更新')
    tgBotTokenDraft.value = ''
    tgBotTokenStatus.value = { configured: true }
    tgBotUsername.value = null
    await verifyBotToken()
  } catch (e) {
    ElMessage.error(`儲存失敗: ${(e as Error).message}`)
  } finally {
    savingTgBotToken.value = false
  }
}

// ---------------------------------------------------------------------------
// Telegram webhook secret (write-only)
// ---------------------------------------------------------------------------
const tgWebhookSecretDraft = ref<string>('')
const tgWebhookSecretStatus = ref<{ configured: boolean }>({ configured: false })
const savingTgWebhookSecret = ref(false)

async function submitTelegramWebhookSecret(): Promise<void> {
  savingTgWebhookSecret.value = true
  try {
    await api.setTelegramWebhookSecret(tgWebhookSecretDraft.value)
    ElMessage.success('Webhook Secret 已更新')
    tgWebhookSecretDraft.value = ''
    tgWebhookSecretStatus.value = { configured: true }
  } catch (e) {
    ElMessage.error(`儲存失敗: ${(e as Error).message}`)
  } finally {
    savingTgWebhookSecret.value = false
  }
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

const RESOLUTIONS = ['1080', '720', '540', '480', '360'] as const
const MODES = ['all', 'latest', 'largest-sn'] as const
const PROXY_PROTOCOLS = ['SOCKS5', 'SOCKS5H', 'HTTP', 'HTTPS'] as const

function freeze(s: WebSettings | null, p: ProxyParts): string {
  if (!s) return ''
  return JSON.stringify({ settings: s, proxy: { ...p } })
}

const dirty = computed(() => {
  if (!settings.value) return false
  return freeze(settings.value, proxyParts) !== snapshot.value
})

async function load(): Promise<void> {
  const data = await api.load()
  settings.value = data
  Object.assign(proxyParts, parseProxy(data.proxy))
  snapshot.value = freeze(settings.value, proxyParts)
}

async function save(): Promise<void> {
  if (!settings.value) return
  saving.value = true
  try {
    const merged: WebSettings = {
      ...settings.value,
      proxy: serializeProxy(proxyParts),
    }
    await api.save(merged)
    ElMessage.success('配置已成功提交')
    await load()
  } catch (err) {
    ElMessage.error(`配置提交失敗: ${(err as Error).message}`)
  } finally {
    saving.value = false
  }
}

async function reload(): Promise<void> {
  try {
    await ElMessageBox.confirm('重新自後端載入配置, 未儲存變更會遺失', '重載配置', {
      confirmButtonText: '確定',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  await load()
  ElMessage.info('配置已重載')
}

function fillCurrentUA(): void {
  if (!settings.value) return
  settings.value.ua = navigator.userAgent
  ElMessage.success('已取得當前瀏覽器 UA')
}

// ---------------------------------------------------------------------------
// Telegram binding
// ---------------------------------------------------------------------------

const tg = useTelegramBinding()

// Whether global Telegram notifications are enabled (admin-configured).
const telegramEnabled = computed(() => settings.value?.telegram?.enabled ?? false)

async function handleTelegramUnlink(): Promise<void> {
  try {
    await ElMessageBox.confirm('確定要解除 Telegram 綁定嗎？', '解除綁定', {
      confirmButtonText: '確定',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  await tg.unlink()
  if (!tg.error.value) {
    ElMessage.success('已解除 Telegram 綁定')
  } else {
    ElMessage.error(`解除失敗: ${tg.error.value}`)
  }
}

async function handleNotifyEnabledChange(val: boolean): Promise<void> {
  await tg.setNotifyEnabled(val)
}

// ---------------------------------------------------------------------------
// Telegram 帳號 — full User API bind (QR / phone login), distinct from the
// notify-only Bot-API binding above. See TgBindDialog.vue for the actual
// QR/phone login flow; this section just shows current status + the
// bind/rebind/unbind actions.
// ---------------------------------------------------------------------------

const tgApi = new TgApi()
const tgSession = ref<TgSession | null>(null)
const tgSessionLoading = ref(false)
const tgBindDialogVisible = ref(false)

async function loadTgSession(): Promise<void> {
  tgSessionLoading.value = true
  try {
    tgSession.value = await tgApi.getSessionStatus()
  } catch {
    // Non-fatal — the feature may simply be unconfigured (503); section
    // stays in its default "未綁定" state.
    tgSession.value = null
  } finally {
    tgSessionLoading.value = false
  }
}

function openTgBindDialog(): void {
  tgBindDialogVisible.value = true
}

function handleTgBound(): void {
  void loadTgSession()
}

async function handleTgUnbind(): Promise<void> {
  try {
    await ElMessageBox.confirm('確定要解除 Telegram 帳號綁定嗎？監控中的 Chat 將停止下載新內容。', '解除綁定', {
      confirmButtonText: '確定',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await tgApi.deleteSession()
    ElMessage.success('已解除綁定')
    await loadTgSession()
  } catch (err) {
    ElMessage.error(`解除失敗：${(err as Error).message}`)
  }
}

// ---------------------------------------------------------------------------
// Merged Telegram status — combines the account (User API / QR-phone) state
// from tgSession with the notification (Bot API /start) state from `tg`
// into a single computed label + single primary action, per the UX fix
// that collapses "Telegram 通知綁定" and "Telegram 帳號" into one section.
// ---------------------------------------------------------------------------

const accountActive = computed(() => tgSession.value?.status === 'active')
const accountFailedOrRevoked = computed(
  () => tgSession.value?.status === 'expired' || tgSession.value?.status === 'revoked',
)

const mergedStatusLabel = computed(() => {
  if (accountFailedOrRevoked.value) return '已失效'
  if (accountActive.value && tg.bound.value) return '完整綁定'
  if (accountActive.value && !tg.bound.value) return '帳號已綁定，通知綁定失敗'
  if (!accountActive.value && tg.bound.value) return '僅通知已綁定'
  return '未綁定'
})

const mergedStatusClass = computed(() => {
  if (mergedStatusLabel.value === '完整綁定' || mergedStatusLabel.value === '僅通知已綁定') {
    return 'ag-tg-bound'
  }
  if (mergedStatusLabel.value === '未綁定') return ''
  return 'ag-tg-pending' // 已失效 / 帳號已綁定，通知綁定失敗
})

// Single primary action button label — null means "no bind/upgrade action
// available" (either fully bound, or notifications globally disabled).
const mergedActionLabel = computed<string | null>(() => {
  if (accountFailedOrRevoked.value) return '重新綁定 Telegram'
  if (accountActive.value && tg.bound.value) return null
  if (accountActive.value && !tg.bound.value) return '重新綁定通知'
  if (tg.bound.value) return '升級為完整綁定'
  if (!telegramEnabled.value) return null
  return '綁定 Telegram'
})

const accountHandleText = computed(() => {
  if (!accountActive.value) return '未綁定'
  if (tgSession.value?.telegram_handle) return `@${tgSession.value.telegram_handle}`
  if (tgSession.value?.phone_tail4) return `***${tgSession.value.phone_tail4}`
  return '—'
})

// Specific reason shown in the "帳號已綁定，通知綁定失敗" tooltip — driven by
// TgSession.notification_bind_status (mirrors backend NotificationBindResult).
// A null/undefined status (legacy row bound before this field existed, or no
// bind was ever attempted) falls back to the generic hint.
const NOTIFY_FAILED_REASON: Partial<Record<TgNotificationBindStatus, string>> = {
  bot_username_not_configured: 'Bot Username 尚未設定',
  bot_username_invalid: 'Bot Username 格式錯誤（應為 @xxx，4-32 字元）',
  bot_not_found: '找不到該 bot，請確認 Bot Username 正確',
  flood_wait: 'Telegram flood limit，請稍候再試',
}

const notifyFailedTooltip = computed(() => {
  const status = tgSession.value?.notification_bind_status
  const detail = tgSession.value?.notification_bind_error
  if (status === 'telegram_error') return `Telegram 錯誤: ${detail ?? ''}`
  if (status === 'unknown_error') return `未知錯誤: ${detail ?? ''}`
  if (status && NOTIFY_FAILED_REASON[status]) return NOTIFY_FAILED_REASON[status]
  return 'Bot Username 可能未設定或錯誤'
})

/** Routes to whichever unbind call matches the current binding: full
 * account session (User API) when one exists, else the legacy
 * notification-only (/start) binding. */
async function handleMergedUnbind(): Promise<void> {
  if (accountActive.value || accountFailedOrRevoked.value) {
    await handleTgUnbind()
  } else {
    await handleTelegramUnlink()
  }
}

// ---------------------------------------------------------------------------
// Retry notification bind — "重試通知綁定" button shown next to the
// "帳號已綁定，通知綁定失敗" status. Re-runs NotificationBinder.bind() against
// the already-bound account session via POST /api/tg/session/rebind-notification.
// ---------------------------------------------------------------------------

const rebindingNotification = ref(false)

async function handleRebindNotification(): Promise<void> {
  rebindingNotification.value = true
  try {
    const result = await tgApi.rebindNotification()
    await loadTgSession()
    if (result.notification_bind_status === 'success') {
      ElMessage.success('通知綁定成功')
    } else {
      ElMessage.error(`通知綁定失敗：${notifyFailedTooltip.value}`)
    }
  } catch (err) {
    ElMessage.error(`重試失敗：${(err as Error).message}`)
  } finally {
    rebindingNotification.value = false
  }
}

// ---------------------------------------------------------------------------
// Admin Telegram Bot Settings
// ---------------------------------------------------------------------------

const _NOTIFY_OPTIONS = ['completed', 'failed', 'cancelled'] as const

const NOTIFY_LABELS: Record<string, string> = {
  completed: '下載完成',
  failed: '下載失敗',
  cancelled: '下載取消',
}

const tgBotUsername = ref<string | null>(null)
const tgBotLoading = ref(false)
const tgWebhookLoading = ref(false)
const tgWebhookDialogVisible = ref(false)
const tgWebhookInfo = ref<TelegramWebhookInfo | null>(null)

// Action buttons are enabled only when telegram.enabled is true in the form.
const tgActionsDisabled = computed(() => !settings.value?.telegram?.enabled)

// ---------------------------------------------------------------------------
// Bot Username — proactive "尚未設定" warning + input normalization/validation.
// An unset/malformed bot_username is the leading cause of "帳號已綁定，通知綁
// 定失敗" (see NOTIFY_FAILED_REASON above) — this surfaces the problem before
// the user even attempts a bind, instead of only reactively via the tooltip.
// ---------------------------------------------------------------------------

const BOT_USERNAME_WARNING_DISMISS_KEY = 'dismissed-bot-username-warning'

function readDismissedWarning(): boolean {
  if (typeof localStorage === 'undefined') return false
  return localStorage.getItem(BOT_USERNAME_WARNING_DISMISS_KEY) === '1'
}

const botUsernameWarningDismissed = ref(readDismissedWarning())

const showBotUsernameWarning = computed(() => {
  if (botUsernameWarningDismissed.value) return false
  if (!settings.value?.telegram?.enabled) return false
  return !settings.value.telegram.bot_username?.trim()
})

function dismissBotUsernameWarning(): void {
  botUsernameWarningDismissed.value = true
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(BOT_USERNAME_WARNING_DISMISS_KEY, '1')
  }
}

function handleBotUsernameBlur(): void {
  if (!settings.value) return
  settings.value.telegram.bot_username = normalizeBotUsername(settings.value.telegram.bot_username)
}

// Validation feedback shown on the Bot Username el-form-item. Empty is not
// an error (the field is optional — see showBotUsernameWarning for the
// "should probably set this" nudge instead); only a non-empty value that
// doesn't match Telegram's username shape is flagged.
const botUsernameError = computed(() => {
  const value = settings.value?.telegram?.bot_username?.trim() ?? ''
  if (!value) return ''
  return isValidBotUsername(value) ? '' : 'Bot Username 格式錯誤，應為 @xxx（4-32 個英數字/底線）'
})

function generateSecret(): void {
  const arr = new Uint8Array(32)
  crypto.getRandomValues(arr)
  tgWebhookSecretDraft.value = Array.from(arr)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

async function verifyBotToken(): Promise<void> {
  tgBotLoading.value = true
  tgBotUsername.value = null
  try {
    const me = await getBotMe()
    tgBotUsername.value = me.username ?? me.first_name ?? String(me.id)
  } catch (err) {
    ElMessage.error(`驗證失敗: ${(err as Error).message}`)
  } finally {
    tgBotLoading.value = false
  }
}

async function handleRegisterWebhook(): Promise<void> {
  tgWebhookLoading.value = true
  try {
    const result = await registerWebhook()
    ElMessage.success(`Webhook 已註冊: ${result.url}`)
    ElMessage({
      message:
        'Webhook 已註冊。若剛才變更過 bot token，建議重新啟動 scheduler 以讓通知使用新 token。',
      type: 'info',
      duration: 6000,
    })
  } catch (err) {
    ElMessage.error(`註冊失敗: ${(err as Error).message}`)
  } finally {
    tgWebhookLoading.value = false
  }
}

async function handleWebhookStatus(): Promise<void> {
  tgWebhookLoading.value = true
  try {
    tgWebhookInfo.value = await getWebhookInfo()
    tgWebhookDialogVisible.value = true
  } catch (err) {
    ElMessage.error(`查詢失敗: ${(err as Error).message}`)
  } finally {
    tgWebhookLoading.value = false
  }
}

onMounted(async () => {
  await load()
  await tg.loadStatus()
  await loadTgSession()
  if (isAdmin.value) {
    try {
      cookieStatus.value = await api.getCookieStatus()
    } catch {
      // Non-fatal — status badge stays at default (false)
    }
    try {
      bilibiliCookieStatus.value = await api.getBilibiliCookieStatus()
    } catch {
      // Non-fatal — status badge stays at default (false)
    }
    try {
      putioTokenStatus.value = await api.getPutioTokenStatus()
    } catch {
      // Non-fatal — status badge stays at default (false)
    }
    try {
      tgBotTokenStatus.value = await api.getTelegramBotTokenStatus()
    } catch {
      // Non-fatal — status badge stays at default (false)
    }
    try {
      tgWebhookSecretStatus.value = await api.getTelegramWebhookSecretStatus()
    } catch {
      // Non-fatal — status badge stays at default (false)
    }
  }
  // Auto-verify bot token for admin only when bot is enabled and a token is configured.
  if (isAdmin.value && settings.value?.telegram?.enabled && tgBotTokenStatus.value.configured) {
    await verifyBotToken()
  }
})

onUnmounted(() => {
  tg.dispose()
})
</script>

<template>
  <div class="ag-container">
    <el-skeleton
      v-if="!settings"
      :rows="8"
      animated
    />

    <template v-else>
      <!-- tab-position must stay "top" — the panes below are empty (see the
           comment on the sibling el-form) so there's no pane content beside
           a vertical nav for "left"/"right" to lay out against. -->
      <el-tabs
        v-model="activeTab"
        tab-position="top"
        class="ag-settings-tabs"
      >
        <el-tab-pane
          v-for="tab in visibleTabs"
          :key="tab.id"
          :label="tab.label"
          :name="tab.id"
        />
      </el-tabs>

      <el-form
        :label-position="isMobile ? 'top' : 'right'"
        :label-width="isMobile ? 'auto' : '200px'"
        size="default"
      >
        <!--
          El-tabs is used for the nav header only — its panes are left empty.
          Element Plus keeps every visited pane mounted (toggled via v-show)
          once activated, so a <transition> placed inside a pane never sees a
          mount/unmount and would not animate on tab switches (see
          BtView.vue for the same pattern). The actual tab content lives in
          this sibling block instead, gated by `activeTab` via v-if/v-else-if
          so exactly one branch is rendered at a time and the slide
          transition fires on every switch. Content stays inline (not split
          into subcomponents) since every field here is bound directly to
          the shared `settings` / `proxyParts` state — switching tabs never
          loses in-progress edits because that state lives on this
          component, not inside whatever happens to be mounted.
        -->
        <transition
          :name="`slide-${slideDir}`"
          mode="out-in"
        >
          <div
            :key="activeTab"
            class="ag-tab-content"
          >
            <!-- ============================================================
                 一般 — 路徑設定 + 下載設定 + 代理設定 + 其他（admin only）
                 ============================================================ -->
            <template v-if="activeTab === 'general' && isAdmin">
              <section class="ag-section">
                <h2 class="ag-section-title">
                  路徑設定
                </h2>
                <el-form-item label="下載目錄">
                  <el-input
                    v-model="settings.bangumi_dir"
                    placeholder="放空則存放於程式所在資料夾"
                  />
                </el-form-item>
                <el-form-item label="暫存目錄">
                  <el-input
                    v-model="settings.temp_dir"
                    placeholder="放空則存放於程式所在資料夾"
                  />
                </el-form-item>
              </section>

              <section class="ag-section">
                <h2 class="ag-section-title">
                  下載設定
                </h2>
                <el-row :gutter="16">
                  <el-col :md="8">
                    <el-form-item label="建立番劇資料夾">
                      <el-switch v-model="settings.classify_bangumi" />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="鎖定解析度">
                      <el-switch v-model="settings.lock_resolution" />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="分段下載模式">
                      <el-switch v-model="settings.segment_download_mode" />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="檔名附加番劇名">
                      <el-switch v-model="settings.add_bangumi_name_to_video_filename" />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="檔名附加解析度">
                      <el-switch v-model="settings.add_resolution_to_video_filename" />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="模擬手機端解析">
                      <el-switch v-model="settings.use_mobile_api" />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="下載彈幕">
                      <el-switch v-model="settings.danmu" />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="下載解析度">
                      <el-select v-model="settings.download_resolution">
                        <el-option
                          v-for="r in RESOLUTIONS"
                          :key="r"
                          :label="`${r}P`"
                          :value="r"
                        />
                      </el-select>
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="預設下載模式">
                      <el-select v-model="settings.default_download_mode">
                        <el-option
                          v-for="m in MODES"
                          :key="m"
                          :label="m"
                          :value="m"
                        />
                      </el-select>
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="更新間隔（分）">
                      <el-input-number
                        v-model="settings.check_frequency"
                        :min="1"
                        :step="1"
                      />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="最大同時下載數">
                      <el-input-number
                        v-model="settings['multi-thread']"
                        :min="1"
                        :step="1"
                      />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="最大同時分段數">
                      <el-input-number
                        v-model="settings.multi_downloading_segment"
                        :min="1"
                        :step="1"
                      />
                    </el-form-item>
                  </el-col>
                  <el-col :md="12">
                    <el-form-item label="影片檔名前綴">
                      <el-input v-model="settings.customized_video_filename_prefix" />
                    </el-form-item>
                  </el-col>
                  <el-col :md="12">
                    <el-form-item label="影片檔名後綴">
                      <el-input v-model="settings.customized_video_filename_suffix" />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="下載冷卻時間（秒）">
                      <el-input-number
                        v-model="settings.download_cd"
                        :min="0"
                        :step="1"
                      />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="單集解析冷卻時間（秒）">
                      <el-input-number
                        v-model="settings.parse_sn_cd"
                        :min="0"
                        :step="1"
                      />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="解析冷卻時間（秒）">
                      <el-input-number
                        v-model="settings.parse_cd"
                        :min="0"
                        :step="1"
                      />
                    </el-form-item>
                  </el-col>
                  <el-col :md="24">
                    <el-form-item label="請求 UA">
                      <div class="ag-ua-row">
                        <el-input
                          v-model="settings.ua"
                          class="ag-ua-input"
                        />
                        <el-button
                          type="primary"
                          @click="fillCurrentUA"
                        >
                          取得當前 UA
                        </el-button>
                      </div>
                    </el-form-item>
                  </el-col>
                </el-row>
              </section>

              <section class="ag-section">
                <h2 class="ag-section-title">
                  代理設定
                </h2>
                <el-form-item label="代理總開關">
                  <el-switch v-model="settings.use_proxy" />
                </el-form-item>
                <el-row :gutter="16">
                  <el-col :md="8">
                    <el-form-item label="選擇協議">
                      <el-select
                        v-model="proxyParts.protocol"
                        :disabled="!settings.use_proxy"
                      >
                        <el-option
                          v-for="p in PROXY_PROTOCOLS"
                          :key="p"
                          :label="p"
                          :value="p"
                        />
                      </el-select>
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="伺服器 IP">
                      <el-input
                        v-model="proxyParts.ip"
                        :disabled="!settings.use_proxy"
                        placeholder="127.0.0.1"
                      />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="Port">
                      <el-input
                        v-model="proxyParts.port"
                        :disabled="!settings.use_proxy"
                        placeholder="1080"
                      />
                    </el-form-item>
                  </el-col>
                  <el-col :md="12">
                    <el-form-item label="帳號">
                      <el-input
                        v-model="proxyParts.user"
                        :disabled="!settings.use_proxy"
                        placeholder="沒有放空即可"
                      />
                    </el-form-item>
                  </el-col>
                  <el-col :md="12">
                    <el-form-item label="密碼">
                      <el-input
                        v-model="proxyParts.password"
                        type="password"
                        :disabled="!settings.use_proxy"
                        placeholder="沒有放空即可"
                        show-password
                      />
                    </el-form-item>
                  </el-col>
                </el-row>
              </section>

              <section class="ag-section">
                <h2 class="ag-section-title">
                  其他
                </h2>
                <el-row :gutter="16">
                  <el-col :md="8">
                    <el-form-item label="每次檢查讀取追番清單">
                      <el-switch v-model="settings.read_sn_list_when_checking_update" />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="每次檢查讀取配置">
                      <el-switch v-model="settings.read_config_when_checking_update" />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="記錄日誌">
                      <el-switch v-model="settings.save_logs" />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="日誌數量">
                      <el-input-number
                        v-model="settings.quantity_of_logs"
                        :min="1"
                        :step="1"
                      />
                    </el-form-item>
                  </el-col>
                </el-row>
              </section>
            </template>

            <!-- ============================================================
                 來源 — Bilibili 下載設定 + Cookie + Bilibili Cookie（admin only）
                 ============================================================ -->
            <template v-else-if="activeTab === 'source' && isAdmin">
              <section class="ag-section">
                <h2 class="ag-section-title">
                  Bilibili 下載設定
                </h2>
                <el-row :gutter="16">
                  <el-col :md="8">
                    <el-form-item label="Bilibili 並行分集數">
                      <el-tooltip
                        content="多 P 影片同時下載的分集數"
                        placement="top"
                      >
                        <el-input-number
                          v-model="settings['bilibili-concurrent-parts']"
                          :min="1"
                          :max="5"
                        />
                      </el-tooltip>
                    </el-form-item>
                  </el-col>
                </el-row>
              </section>

              <section class="ag-section">
                <h2 class="ag-section-title">
                  Cookie
                </h2>
                <el-form-item label="Cookie">
                  <div class="cookie-row">
                    <el-tag
                      :type="cookieStatus.configured ? 'success' : 'info'"
                      size="small"
                    >
                      {{ cookieStatus.configured ? '目前已設定' : '尚未設定' }}
                    </el-tag>
                    <el-input
                      v-model="cookieDraft"
                      type="password"
                      show-password
                      placeholder="貼上完整 cookie 字串"
                      :disabled="!isAdmin"
                    />
                    <el-button
                      type="primary"
                      :disabled="!cookieDraft || !isAdmin"
                      :loading="savingCookie"
                      @click="submitCookie"
                    >
                      儲存
                    </el-button>
                  </div>
                  <div class="cookie-hint">
                    Cookie 只有管理員能修改；送出後不會再顯示，請保留備份。
                  </div>
                </el-form-item>
              </section>

              <section class="ag-section">
                <h2 class="ag-section-title">
                  Bilibili Cookie
                </h2>
                <el-form-item label="Bilibili Cookie">
                  <div class="cookie-row">
                    <el-tag
                      :type="bilibiliCookieStatus.configured ? 'success' : 'info'"
                      size="small"
                    >
                      {{ bilibiliCookieStatus.configured ? '目前已設定' : '尚未設定' }}
                    </el-tag>
                    <el-input
                      v-model="bilibiliCookieDraft"
                      type="password"
                      show-password
                      placeholder="貼上瀏覽器中 bilibili.com 的 cookie（k=v; k=v; 格式，至少包含 SESSDATA）"
                      :disabled="!isAdmin"
                    />
                    <el-button
                      type="primary"
                      :disabled="!bilibiliCookieDraft || !isAdmin"
                      :loading="savingBilibiliCookie"
                      @click="submitBilibiliCookie"
                    >
                      儲存
                    </el-button>
                  </div>
                  <div class="cookie-hint">
                    Bilibili Cookie 只有管理員能修改；送出後不會再顯示，請保留備份。
                  </div>
                </el-form-item>
              </section>
            </template>

            <!-- ============================================================
                 BT 下載 — BT 下載設定（含 Put.io Token，admin only）
                 ============================================================ -->
            <template v-else-if="activeTab === 'bt' && isAdmin">
              <section class="ag-section">
                <h2 class="ag-section-title">
                  BT 下載設定
                </h2>
                <el-row :gutter="16">
                  <el-col :md="8">
                    <el-form-item label="啟用 BT 下載">
                      <el-switch v-model="settings['bt-downloader'].enabled" />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="RSS 掃描間隔（秒）">
                      <el-input-number
                        v-model="settings['bt-downloader']['poll-interval-seconds']"
                        :min="60"
                        :max="3600"
                        :step="60"
                      />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="落地輪詢間隔（秒）">
                      <el-input-number
                        v-model="settings['bt-downloader']['landing-poll-seconds']"
                        :min="30"
                        :max="600"
                        :step="30"
                      />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="簡繁轉換">
                      <el-tooltip
                        content="簡→繁轉換後再與關鍵字比對"
                        placement="top"
                      >
                        <el-switch v-model="settings['bt-downloader']['hanzi-convert']" />
                      </el-tooltip>
                    </el-form-item>
                  </el-col>
                  <el-col :md="16">
                    <el-form-item label="落地目錄">
                      <el-input
                        v-model="settings['bt-downloader']['landing-dir']"
                        placeholder="留空則使用番劇資料夾"
                      />
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="條目保留天數">
                      <el-tooltip
                        content="超過此天數的抓取紀錄會被每日清理，也是自動重掃未命中條目的範圍。"
                        placement="top"
                      >
                        <el-input-number
                          v-model="settings['bt-downloader']['entry-retention-days']"
                          :min="1"
                        />
                      </el-tooltip>
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="任務歷史保留天數">
                      <el-tooltip
                        content="下載任務歷史紀錄超過此天數會被每日清理。"
                        placement="top"
                      >
                        <el-input-number
                          v-model="settings['bt-downloader']['task-history-retention-days']"
                          :min="1"
                        />
                      </el-tooltip>
                    </el-form-item>
                  </el-col>
                  <el-col :md="8">
                    <el-form-item label="落地後自動刪除 Put.io 遠端檔案">
                      <el-tooltip
                        content="落地成功後自動從 Put.io 移除該檔案以節省空間。本地檔案不會被影響。預設開啟。"
                        placement="top"
                      >
                        <el-switch v-model="settings['bt-downloader']['auto-delete-remote-on-landed']" />
                      </el-tooltip>
                    </el-form-item>
                  </el-col>
                </el-row>

                <el-form-item label="Put.io Token">
                  <div class="cookie-row">
                    <el-tag
                      :type="putioTokenStatus.configured ? 'success' : 'info'"
                      size="small"
                    >
                      {{ putioTokenStatus.configured ? '目前已設定' : '尚未設定' }}
                    </el-tag>
                    <el-input
                      v-model="putioTokenDraft"
                      type="password"
                      show-password
                      placeholder="貼上 Put.io OAuth token"
                      :disabled="!isAdmin"
                    />
                    <el-button
                      type="primary"
                      :disabled="!putioTokenDraft || !isAdmin"
                      :loading="savingPutioToken"
                      @click="submitPutioToken"
                    >
                      儲存
                    </el-button>
                  </div>
                  <div class="cookie-hint">
                    Put.io token 只有管理員能修改；送出後不會再顯示，請保留備份。
                  </div>
                </el-form-item>
              </section>
            </template>

            <!-- ============================================================
                 Telegram — merged account (QR/phone User API) + notification
                 (Bot API /start) binding status, visible to every user. The
                 admin-only Telegram Bot 設定 section (bot token, webhook,
                 bot username, notify-on triggers, ...) is nested below it.
                 ================================================================ -->
            <template v-else-if="activeTab === 'telegram'">
              <section class="ag-section">
                <h2 class="ag-section-title">
                  Telegram
                </h2>

                <!-- Bot not configured by admin (legacy /start path) -->
                <template v-if="tg.notConfigured.value">
                  <span class="ag-muted">系統管理員尚未設定 Telegram bot，無法綁定。</span>
                </template>

                <!-- Legacy link pending — defensive: the button that used to start
                     this flow was removed from the UI (superseded by the QR/phone
                     dialog below), but a link started before this change may still
                     be waiting for confirmation. -->
                <template v-else-if="tg.linkPending.value">
                  <div class="ag-tg-row">
                    <span class="ag-tg-status ag-tg-pending">等待 Telegram 確認...</span>
                    <span class="ag-muted">剩餘 {{ tg.countdownLabel.value }}</span>
                  </div>
                  <div class="ag-muted ag-tg-hint">
                    請到剛才開啟的 Telegram 分頁按下「啟動 / Start」
                  </div>
                  <el-button
                    type="default"
                    :loading="tg.loading.value"
                    @click="handleTelegramUnlink"
                  >
                    取消綁定
                  </el-button>
                </template>

                <!-- Normal state — merged status + account/notify detail rows -->
                <template v-else>
                  <div class="ag-tg-account-row">
                    <span class="ag-tg-account-label">狀態</span>
                    <el-tooltip
                      :content="notifyFailedTooltip"
                      placement="top"
                      :disabled="mergedStatusLabel !== '帳號已綁定，通知綁定失敗'"
                    >
                      <span
                        class="ag-tg-status"
                        :class="mergedStatusClass"
                      >{{ mergedStatusLabel }}</span>
                    </el-tooltip>
                    <el-button
                      v-if="mergedStatusLabel === '帳號已綁定，通知綁定失敗'"
                      size="small"
                      :loading="rebindingNotification"
                      @click="handleRebindNotification"
                    >
                      重試通知綁定
                    </el-button>
                  </div>

                  <div class="ag-tg-account-row">
                    <span class="ag-tg-account-label">帳號</span>
                    <span>{{ accountHandleText }}</span>
                  </div>

                  <div class="ag-tg-account-row">
                    <span class="ag-tg-account-label">通知</span>
                    <el-switch
                      v-if="tg.bound.value"
                      :model-value="tg.notifyEnabled.value"
                      @update:model-value="handleNotifyEnabledChange"
                    />
                    <span v-else>未綁定</span>
                  </div>

                  <div
                    v-if="tgSession?.last_active_at"
                    class="ag-tg-account-row"
                  >
                    <span class="ag-tg-account-label">上次活躍</span>
                    <span>{{ tgSession.last_active_at }}</span>
                  </div>

                  <!-- Warning when notifications are bound but globally disabled -->
                  <div
                    v-if="tg.bound.value && !telegramEnabled"
                    class="ag-tg-disabled-warning"
                  >
                    ⚠️ 系統 Telegram 通知目前停用中，綁定但無法收到通知
                  </div>

                  <!-- Nothing bound at all and the bot itself is disabled -->
                  <span
                    v-if="!tg.bound.value && !accountActive && !telegramEnabled"
                    class="ag-muted"
                  >系統管理員尚未啟用 Telegram 通知功能。</span>

                  <div class="ag-tg-account-actions">
                    <el-button
                      v-if="mergedActionLabel"
                      type="primary"
                      :loading="tgSessionLoading || tg.loading.value"
                      @click="openTgBindDialog"
                    >
                      {{ mergedActionLabel }}
                    </el-button>
                    <el-button
                      v-if="accountActive || accountFailedOrRevoked || tg.bound.value"
                      type="danger"
                      :loading="tg.loading.value"
                      @click="handleMergedUnbind"
                    >
                      解除綁定
                    </el-button>
                  </div>

                  <span
                    v-if="tg.error.value"
                    class="ag-muted"
                  >{{ tg.error.value }}</span>
                </template>

                <TgBindDialog
                  v-model="tgBindDialogVisible"
                  @bound="handleTgBound"
                />
              </section>

              <!-- ============================================================
                   Admin-only: Telegram Bot 設定
                   ============================================================ -->
              <template v-if="isAdmin">
                <section class="ag-section">
                  <h2 class="ag-section-title">
                    Telegram Bot 設定
                  </h2>

                  <el-alert
                    v-if="showBotUsernameWarning"
                    type="warning"
                    show-icon
                    class="ag-bot-username-warning"
                    @close="dismissBotUsernameWarning"
                  >
                    <template #title>
                      <div>
                        <strong>⚠️ Bot Username 尚未設定</strong>
                        <div class="ag-muted">
                          沒有設定 Bot Username 會導致「Telegram 帳號綁定」的自動通知綁定失敗——
                          用戶完成 QR / 手機驗證碼後，需要手動走舊的 /start 流程綁通知。
                          請填入你的通知 bot 的 Telegram username（含 @ 前綴）。
                        </div>
                      </div>
                    </template>
                  </el-alert>

                  <el-form-item label="啟用 Bot">
                    <el-switch v-model="settings.telegram.enabled" />
                  </el-form-item>

                  <el-form-item label="Bot Token">
                    <div class="cookie-row">
                      <el-tag
                        :type="tgBotTokenStatus.configured ? 'success' : 'info'"
                        size="small"
                      >
                        {{ tgBotTokenStatus.configured ? '目前已設定' : '尚未設定' }}
                      </el-tag>
                      <el-input
                        v-model="tgBotTokenDraft"
                        type="password"
                        show-password
                        placeholder="輸入新的 Bot Token"
                      />
                      <el-button
                        type="primary"
                        :disabled="!tgBotTokenDraft"
                        :loading="savingTgBotToken"
                        @click="submitTelegramBotToken"
                      >
                        儲存
                      </el-button>
                      <el-tooltip
                        :content="tgActionsDisabled ? '請先啟用 Bot 再執行此操作' : ''"
                        :disabled="!tgActionsDisabled"
                        placement="top"
                      >
                        <el-button
                          :disabled="tgActionsDisabled"
                          :loading="tgBotLoading"
                          @click="verifyBotToken"
                        >
                          驗證 Bot Token
                        </el-button>
                      </el-tooltip>
                    </div>
                    <div class="cookie-hint">
                      Bot Token 只有管理員能修改；送出後不會再顯示，請保留備份。
                    </div>
                    <div
                      v-if="tgBotUsername"
                      class="ag-muted"
                    >
                      Bot: @{{ tgBotUsername }}
                    </div>
                  </el-form-item>

                  <el-form-item
                    label="Bot Username"
                    :error="botUsernameError"
                  >
                    <el-input
                      v-model="settings.telegram.bot_username"
                      placeholder="@YourBotUsername"
                      clearable
                      @blur="handleBotUsernameBlur"
                    />
                    <div class="cookie-hint">
                      你的通知 bot 的 Telegram username。用於 Telegram 下載器的「一綁全綁」功能——
                      用戶完成 QR / 手機驗證碼綁定後，系統會自動用他們的 session 傳
                      <code>/start</code> 給這個 bot 完成通知綁定，不用另外走舊流程。
                      <br />
                      不設此欄位不影響現有 <code>/start</code> 手動綁定通知的流程。
                    </div>
                  </el-form-item>

                  <el-form-item label="Webhook Secret">
                    <div class="cookie-row">
                      <el-tag
                        :type="tgWebhookSecretStatus.configured ? 'success' : 'info'"
                        size="small"
                      >
                        {{ tgWebhookSecretStatus.configured ? '目前已設定' : '尚未設定' }}
                      </el-tag>
                      <el-input
                        v-model="tgWebhookSecretDraft"
                        type="password"
                        show-password
                        placeholder="輸入新的 Webhook 密鑰"
                      />
                      <el-button @click="generateSecret">
                        產生
                      </el-button>
                      <el-button
                        type="primary"
                        :disabled="!tgWebhookSecretDraft"
                        :loading="savingTgWebhookSecret"
                        @click="submitTelegramWebhookSecret"
                      >
                        儲存
                      </el-button>
                    </div>
                    <div class="cookie-hint">
                      Webhook Secret 只有管理員能修改；送出後不會再顯示，請保留備份。
                    </div>
                  </el-form-item>

                  <el-form-item label="Public URL">
                    <el-input
                      v-model="settings.telegram.public_url"
                      placeholder="https://example.com"
                    />
                  </el-form-item>

                  <el-row :gutter="16">
                    <el-col :md="8">
                      <el-form-item label="Admin 廣播">
                        <el-switch v-model="settings.telegram.admin_broadcast" />
                      </el-form-item>
                    </el-col>
                    <el-col :md="8">
                      <el-form-item label="速率限制（/分鐘）">
                        <el-input-number
                          v-model="settings.telegram.rate_limit_per_minute"
                          :min="1"
                          :max="300"
                        />
                      </el-form-item>
                    </el-col>
                  </el-row>

                  <el-form-item label="通知觸發條件">
                    <el-checkbox-group v-model="settings.telegram.notify_on">
                      <el-checkbox
                        v-for="opt in _NOTIFY_OPTIONS"
                        :key="opt"
                        :label="opt"
                        :value="opt"
                      >
                        {{ NOTIFY_LABELS[opt] }}
                      </el-checkbox>
                    </el-checkbox-group>
                  </el-form-item>

                  <div class="ag-tg-actions">
                    <el-tooltip
                      :content="tgActionsDisabled ? '請先啟用 Bot 再執行此操作' : ''"
                      :disabled="!tgActionsDisabled"
                      placement="top"
                    >
                      <el-button
                        type="primary"
                        :disabled="tgActionsDisabled"
                        :loading="tgWebhookLoading"
                        @click="handleRegisterWebhook"
                      >
                        重新註冊 Webhook
                      </el-button>
                    </el-tooltip>
                    <el-tooltip
                      :content="tgActionsDisabled ? '請先啟用 Bot 再執行此操作' : ''"
                      :disabled="!tgActionsDisabled"
                      placement="top"
                    >
                      <el-button
                        :disabled="tgActionsDisabled"
                        :loading="tgWebhookLoading"
                        @click="handleWebhookStatus"
                      >
                        查看 Webhook 狀態
                      </el-button>
                    </el-tooltip>
                  </div>

                  <!-- Webhook info dialog -->
                  <el-dialog
                    v-model="tgWebhookDialogVisible"
                    title="Webhook 狀態"
                    :width="isMobile ? '100%' : '500px'"
                    :fullscreen="isMobile"
                  >
                    <template v-if="tgWebhookInfo">
                      <p><strong>URL:</strong> {{ tgWebhookInfo.url ?? '（未設定）' }}</p>
                      <p><strong>待處理更新數:</strong> {{ tgWebhookInfo.pending_update_count ?? 0 }}</p>
                      <p v-if="tgWebhookInfo.last_error_message">
                        <strong>最後錯誤:</strong> {{ tgWebhookInfo.last_error_message }}
                      </p>
                    </template>
                    <template #footer>
                      <el-button @click="tgWebhookDialogVisible = false">
                        關閉
                      </el-button>
                    </template>
                  </el-dialog>
                </section>
              </template>
            </template>
          </div>
        </transition>
      </el-form>
    </template>

    <!-- DirtyFab is admin-only; non-admin binding changes take effect immediately -->
    <DirtyFab
      v-if="isAdmin"
      :visible="dirty"
      :saving="saving"
      discard-label="重載配置"
      @save="save"
      @discard="reload"
    />
  </div>
</template>

<style scoped>
.ag-settings-tabs {
  margin-bottom: 16px;
}
.ag-tab-content {
  min-width: 0;
}
.ag-ua-row {
  display: flex;
  gap: 8px;
  width: 100%;
}
.ag-ua-input {
  flex: 1;
}
.cookie-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.cookie-row .el-input {
  flex: 1;
}
.cookie-hint {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.ag-muted {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.ag-tg-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}
.ag-tg-status {
  font-weight: bold;
}
.ag-tg-bound {
  color: var(--el-color-success);
}
.ag-tg-pending {
  color: var(--el-color-warning);
}
.ag-tg-hint {
  margin-bottom: 8px;
}
.ag-tg-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
}
.ag-tg-disabled-warning {
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--el-color-warning);
}
.ag-tg-account-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 6px;
}
.ag-tg-account-label {
  width: 80px;
  flex-shrink: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.ag-tg-account-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
}

.slide-left-enter-active,
.slide-left-leave-active,
.slide-right-enter-active,
.slide-right-leave-active {
  transition: opacity 220ms cubic-bezier(0.4, 0, 0.2, 1),
    transform 220ms cubic-bezier(0.4, 0, 0.2, 1);
}
.slide-left-enter-from {
  opacity: 0;
  transform: translateX(24px);
}
.slide-left-leave-to {
  opacity: 0;
  transform: translateX(-24px);
}
.slide-right-enter-from {
  opacity: 0;
  transform: translateX(-24px);
}
.slide-right-leave-to {
  opacity: 0;
  transform: translateX(24px);
}
</style>
