<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ConfigApi, parseProxy, serializeProxy } from '@/api/config'
import type { ProxyParts, TelegramWebhookInfo, WebSettings } from '@/types'
import { useAuthStore } from '@/stores/auth'
import { useTelegramBinding } from '@/composables/useTelegramBinding'
import { getBotMe, getWebhookInfo, registerWebhook } from '@/api/telegram_admin'
import DirtyFab from '@/components/DirtyFab.vue'

const api = new ConfigApi()
const { isAdmin } = useAuthStore()

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

async function handleTelegramBind(): Promise<void> {
  await tg.startLink()
}

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

function generateSecret(): void {
  if (!settings.value) return
  const arr = new Uint8Array(32)
  crypto.getRandomValues(arr)
  settings.value.telegram.webhook_secret = Array.from(arr)
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
  if (isAdmin.value) {
    try {
      cookieStatus.value = await api.getCookieStatus()
    } catch {
      // Non-fatal — status badge stays at default (false)
    }
  }
  // Auto-verify bot token for admin only when bot is enabled and token is set
  if (isAdmin.value && settings.value?.telegram?.enabled && settings.value?.telegram?.bot_token) {
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

    <el-form
      v-else
      label-position="right"
      label-width="200px"
      size="default"
    >
      <!-- ================================================================
           Admin-only sections
           ================================================================ -->
      <template v-if="isAdmin">
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
      </template>

      <!-- ================================================================
           Telegram 通知綁定 — visible to all users
           ================================================================ -->
      <section class="ag-section">
        <h2 class="ag-section-title">
          Telegram 通知綁定
        </h2>

        <!-- Bot not configured by admin -->
        <template v-if="tg.notConfigured.value">
          <span class="ag-muted">系統管理員尚未設定 Telegram bot，無法綁定。</span>
        </template>

        <!-- Already bound -->
        <template v-else-if="tg.bound.value">
          <div class="ag-tg-row">
            <span class="ag-tg-status ag-tg-bound">已綁定</span>
            <span class="ag-muted">下載完成/失敗時會透過 Telegram 私訊通知你</span>
          </div>
          <!-- Warning when globally disabled but already bound -->
          <div
            v-if="!telegramEnabled"
            class="ag-tg-disabled-warning"
          >
            ⚠️ 系統 Telegram 通知目前停用中，綁定但無法收到通知
          </div>
          <el-form-item label="通知啟用">
            <el-switch
              :model-value="tg.notifyEnabled.value"
              @update:model-value="handleNotifyEnabledChange"
            />
          </el-form-item>
          <el-button
            type="danger"
            :loading="tg.loading.value"
            @click="handleTelegramUnlink"
          >
            解除綁定
          </el-button>
        </template>

        <!-- Link pending / waiting for Telegram confirmation -->
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

        <!-- Not bound, no pending link -->
        <template v-else>
          <!-- Telegram globally disabled and not bound -->
          <template v-if="!telegramEnabled">
            <span class="ag-muted">系統管理員尚未啟用 Telegram 通知功能。</span>
          </template>
          <!-- Telegram enabled — show bind button -->
          <template v-else>
            <el-button
              type="primary"
              :loading="tg.loading.value"
              @click="handleTelegramBind"
            >
              綁定 Telegram
            </el-button>
            <span
              v-if="tg.error.value"
              class="ag-muted"
            >{{ tg.error.value }}</span>
          </template>
        </template>
      </section>

      <!-- ================================================================
           Admin-only: Telegram Bot 設定
           ================================================================ -->
      <template v-if="isAdmin">
        <section class="ag-section">
          <h2 class="ag-section-title">
            Telegram Bot 設定
          </h2>

          <el-form-item label="啟用 Bot">
            <el-switch v-model="settings.telegram.enabled" />
          </el-form-item>

          <el-form-item label="Bot Token">
            <div class="ag-ua-row">
              <el-input
                v-model="settings.telegram.bot_token"
                type="password"
                show-password
                placeholder="輸入 Bot Token"
                class="ag-ua-input"
              />
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
            <div
              v-if="tgBotUsername"
              class="ag-muted"
            >
              Bot: @{{ tgBotUsername }}
            </div>
          </el-form-item>

          <el-form-item label="Webhook Secret">
            <div class="ag-ua-row">
              <el-input
                v-model="settings.telegram.webhook_secret"
                type="password"
                show-password
                placeholder="Webhook 密鑰"
                class="ag-ua-input"
              />
              <el-button @click="generateSecret">
                產生
              </el-button>
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
              <el-form-item label="允許本機（開發）">
                <el-tooltip
                  content="僅限開發環境使用，正式環境請關閉"
                  placement="top"
                >
                  <el-switch v-model="settings.telegram.allow_localhost" />
                </el-tooltip>
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
            width="500px"
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
    </el-form>

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
</style>
