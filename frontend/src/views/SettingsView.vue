<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ConfigApi, parseProxy, serializeProxy } from '@/api/config'
import type { ProxyParts, WebSettings } from '@/types'
import { useAuthStore } from '@/stores/auth'
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

onMounted(async () => {
  await load()
  try {
    cookieStatus.value = await api.getCookieStatus()
  } catch {
    // Non-fatal — status badge stays at default (false)
  }
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
    </el-form>

    <DirtyFab
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
</style>
