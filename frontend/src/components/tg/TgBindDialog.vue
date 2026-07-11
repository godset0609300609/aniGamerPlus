<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { TgApi } from '@/api/tg'
import { useBreakpoint } from '@/composables/useBreakpoint'

const props = defineProps<{
  modelValue: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  bound: [handle: string | null]
}>()

const { isMobile } = useBreakpoint()
const api = new TgApi()

const POLL_INTERVAL_MS = 2000

type BindTab = 'qr' | 'phone'
const activeTab = ref<BindTab>('qr')

// ---------------------------------------------------------------------------
// Shared success handling
// ---------------------------------------------------------------------------

function handleSuccess(handle: string | null | undefined): void {
  stopQrPolling()
  ElMessage.success(handle ? `已綁定 @${handle}` : '已綁定 Telegram 帳號')
  emit('bound', handle ?? null)
  emit('update:modelValue', false)
}

// ---------------------------------------------------------------------------
// QR tab
// ---------------------------------------------------------------------------

const qrLoading = ref(false)
const qrError = ref<string | null>(null)
const qrPngDataUri = ref<string | null>(null)
const qrLoginToken = ref<string | null>(null)
const qrAwaitingPassword = ref(false)
const qrPasswordDraft = ref('')
const qrPasswordError = ref<string | null>(null)
const qrSubmittingPassword = ref(false)

let qrPollTimer: ReturnType<typeof setInterval> | null = null

function stopQrPolling(): void {
  if (qrPollTimer !== null) {
    clearInterval(qrPollTimer)
    qrPollTimer = null
  }
}

async function startQrFlow(): Promise<void> {
  stopQrPolling()
  qrError.value = null
  qrAwaitingPassword.value = false
  qrPasswordDraft.value = ''
  qrPasswordError.value = null
  qrLoading.value = true
  try {
    const res = await api.startQrLogin()
    qrLoginToken.value = res.login_token
    qrPngDataUri.value = res.qr_code_png_base64
    qrPollTimer = setInterval(() => void pollQr(), POLL_INTERVAL_MS)
  } catch (err) {
    qrError.value = (err as Error).message
  } finally {
    qrLoading.value = false
  }
}

async function pollQr(): Promise<void> {
  if (!qrLoginToken.value) return
  try {
    const res = await api.pollQrLogin(qrLoginToken.value)
    if (res.status === 'success') {
      handleSuccess(res.telegram_handle)
    } else if (res.status === 'awaiting_password') {
      qrAwaitingPassword.value = true
    } else if (res.status === 'failed') {
      stopQrPolling()
      qrError.value = res.error ?? '綁定失敗，請重試'
    }
  } catch {
    // Transient poll failure — keep polling, don't spam errors.
  }
}

async function submitQrPassword(): Promise<void> {
  if (!qrLoginToken.value) return
  qrSubmittingPassword.value = true
  qrPasswordError.value = null
  try {
    const res = await api.submitQrPassword(qrLoginToken.value, qrPasswordDraft.value)
    if (res.status === 'success') {
      handleSuccess(res.telegram_handle)
    } else {
      qrPasswordError.value = res.error ?? '密碼錯誤，請再試一次'
    }
  } catch (err) {
    qrPasswordError.value = (err as Error).message
  } finally {
    qrSubmittingPassword.value = false
  }
}

// ---------------------------------------------------------------------------
// Phone tab
// ---------------------------------------------------------------------------

type PhoneStep = 'phone' | 'code' | 'password'
const phoneStep = ref<PhoneStep>('phone')
const phoneDraft = ref('')
const phoneCodeDraft = ref('')
const phonePasswordDraft = ref('')
const phoneLoginToken = ref<string | null>(null)
const phoneLoading = ref(false)
const phoneError = ref<string | null>(null)

function resetPhoneFlow(): void {
  phoneStep.value = 'phone'
  phoneDraft.value = ''
  phoneCodeDraft.value = ''
  phonePasswordDraft.value = ''
  phoneLoginToken.value = null
  phoneError.value = null
}

async function sendPhoneCode(): Promise<void> {
  phoneLoading.value = true
  phoneError.value = null
  try {
    const res = await api.startPhoneLogin(phoneDraft.value)
    phoneLoginToken.value = res.login_token
    phoneStep.value = 'code'
  } catch (err) {
    phoneError.value = (err as Error).message
  } finally {
    phoneLoading.value = false
  }
}

async function submitPhoneCode(): Promise<void> {
  if (!phoneLoginToken.value) return
  phoneLoading.value = true
  phoneError.value = null
  try {
    const res = await api.submitPhoneCode(phoneLoginToken.value, phoneCodeDraft.value)
    if (res.status === 'success') {
      handleSuccess(res.telegram_handle)
    } else if (res.status === 'awaiting_password') {
      phoneStep.value = 'password'
    } else if (res.status === 'awaiting_code') {
      // Wrong or expired code — stay on the code step (same login_token is
      // still usable) and surface the error inline so the user can retry
      // without restarting the whole phone-login flow.
      phoneError.value = res.error ?? '驗證碼錯誤，請重新輸入'
    } else {
      phoneError.value = res.error ?? '綁定失敗，請重新開始'
    }
  } catch (err) {
    phoneError.value = (err as Error).message
  } finally {
    phoneLoading.value = false
  }
}

async function submitPhonePassword(): Promise<void> {
  if (!phoneLoginToken.value) return
  phoneLoading.value = true
  phoneError.value = null
  try {
    const res = await api.submitPhonePassword(phoneLoginToken.value, phonePasswordDraft.value)
    if (res.status === 'success') {
      handleSuccess(res.telegram_handle)
    } else {
      phoneError.value = res.error ?? '密碼錯誤，請再試一次'
    }
  } catch (err) {
    phoneError.value = (err as Error).message
  } finally {
    phoneLoading.value = false
  }
}

// ---------------------------------------------------------------------------
// Lifecycle — start the QR flow whenever the dialog opens on the QR tab;
// stop polling whenever the dialog closes or the tab switches away.
// ---------------------------------------------------------------------------

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      activeTab.value = 'qr'
      resetPhoneFlow()
      void startQrFlow()
    } else {
      stopQrPolling()
    }
  },
)

watch(activeTab, (tab) => {
  if (tab === 'qr') {
    void startQrFlow()
  } else {
    stopQrPolling()
  }
})

onUnmounted(stopQrPolling)

function close(): void {
  emit('update:modelValue', false)
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="綁定 Telegram 帳號"
    :width="isMobile ? '100%' : '440px'"
    :fullscreen="isMobile"
    @update:model-value="(val: boolean) => emit('update:modelValue', val)"
  >
    <el-tabs v-model="activeTab">
      <el-tab-pane
        label="📱 QR 綁定"
        name="qr"
      >
        <div class="ag-tg-bind-qr">
          <el-alert
            v-if="qrError"
            type="error"
            :closable="false"
            class="ag-tg-bind-error"
          >
            <template #title>
              {{ qrError }}
            </template>
          </el-alert>

          <template v-if="!qrAwaitingPassword">
            <div class="ag-tg-qr-frame">
              <el-icon
                v-if="qrLoading"
                class="ag-tg-qr-spinner"
                :size="32"
              >
                <Loading />
              </el-icon>
              <img
                v-else-if="qrPngDataUri"
                :src="qrPngDataUri"
                alt="Telegram 登入 QR code"
                class="ag-tg-qr-image"
              />
            </div>
            <p class="ag-tg-qr-hint">
              請用手機 Telegram：設定 → 裝置 → 掃描 QR Code 掃描此圖
            </p>
            <el-button
              v-if="qrError"
              type="primary"
              @click="startQrFlow"
            >
              重新產生 QR Code
            </el-button>
          </template>

          <!-- 2FA -->
          <template v-else>
            <p class="ag-tg-qr-hint">
              此帳號已啟用兩步驟驗證，請輸入密碼
            </p>
            <el-form-item label="密碼">
              <el-input
                v-model="qrPasswordDraft"
                type="password"
                show-password
                @keyup.enter="submitQrPassword"
              />
            </el-form-item>
            <el-alert
              v-if="qrPasswordError"
              type="error"
              :closable="false"
              class="ag-tg-bind-error"
            >
              <template #title>
                {{ qrPasswordError }}
              </template>
            </el-alert>
            <el-button
              type="primary"
              :loading="qrSubmittingPassword"
              @click="submitQrPassword"
            >
              確認
            </el-button>
          </template>
        </div>
      </el-tab-pane>

      <el-tab-pane
        label="🔢 手機驗證碼"
        name="phone"
      >
        <div class="ag-tg-bind-phone">
          <el-alert
            v-if="phoneError"
            type="error"
            :closable="false"
            class="ag-tg-bind-error"
          >
            <template #title>
              {{ phoneError }}
            </template>
          </el-alert>

          <template v-if="phoneStep === 'phone'">
            <el-form-item label="手機號碼">
              <el-input
                v-model="phoneDraft"
                placeholder="+886912345678"
                @keyup.enter="sendPhoneCode"
              />
            </el-form-item>
            <el-button
              type="primary"
              :loading="phoneLoading"
              :disabled="!phoneDraft"
              @click="sendPhoneCode"
            >
              傳送驗證碼
            </el-button>
          </template>

          <template v-else-if="phoneStep === 'code'">
            <el-form-item label="驗證碼">
              <el-input
                v-model="phoneCodeDraft"
                placeholder="請輸入 Telegram 傳送的驗證碼"
                @keyup.enter="submitPhoneCode"
              />
            </el-form-item>
            <el-button
              type="primary"
              :loading="phoneLoading"
              :disabled="!phoneCodeDraft"
              @click="submitPhoneCode"
            >
              驗證
            </el-button>
          </template>

          <template v-else>
            <p class="ag-tg-qr-hint">
              此帳號已啟用兩步驟驗證，請輸入密碼
            </p>
            <el-form-item label="密碼">
              <el-input
                v-model="phonePasswordDraft"
                type="password"
                show-password
                @keyup.enter="submitPhonePassword"
              />
            </el-form-item>
            <el-button
              type="primary"
              :loading="phoneLoading"
              @click="submitPhonePassword"
            >
              確認
            </el-button>
          </template>
        </div>
      </el-tab-pane>
    </el-tabs>

    <div class="ag-tg-bind-footer">
      <el-button @click="close">
        關閉
      </el-button>
    </div>
  </el-dialog>
</template>

<style scoped>
.ag-tg-bind-qr,
.ag-tg-bind-phone {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 8px;
}
.ag-tg-bind-error {
  margin-bottom: 4px;
}
.ag-tg-qr-frame {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 220px;
  border: 1px solid var(--el-border-color, #e4e7ed);
  border-radius: 8px;
  padding: 12px;
  background: #fff;
}
.ag-tg-qr-image {
  width: 200px;
  height: 200px;
  max-width: 100%;
}
.ag-tg-qr-spinner {
  animation: ag-tg-spin 1s linear infinite;
  color: var(--el-color-primary);
}
@keyframes ag-tg-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
.ag-tg-qr-hint {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  text-align: center;
  margin: 0;
}
.ag-tg-bind-footer {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
