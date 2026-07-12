<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ConfigApi } from '@/api/config'
import { TasksApi } from '@/api/tasks'
import { useBreakpoint } from '@/composables/useBreakpoint'
import type { ManualTaskRequest, Resolution } from '@/types'

/**
 * QuickDownloadView — lightweight landing page opened as a popup by the
 * Tampermonkey userscript injected on 動畫瘋 anime pages (see
 * BrowserExtensionDialog.vue for the injected snippet). Mirrors
 * QuickAddView.vue's lifecycle/close/toast/error-card semantics, but
 * enqueues a manual download of *this* episode (POST /api/tasks/manual)
 * instead of adding a tracked series to the anime list.
 *
 * By design this is a one-click flow: the only control is the resolution
 * dropdown (prefilled from the server's configured default), everything
 * else (mode/thread/classify/danmu) is a fixed sensible default for a
 * single-episode quick download.
 *
 * There is intentionally no admin "owner" picker here (unlike
 * QuickAddView's owner select for anime-list entries): `ManualTaskRequest`
 * has no owner field, and `POST /api/tasks/manual` always attributes the
 * enqueued task to the authenticated caller (see backend
 * `app/api/tasks_api.py::manual_task` / `TaskService.enqueue`) — there is
 * no backend concept of submitting a manual task "on behalf of" another
 * user, so no such control is offered here.
 */

const route = useRoute()
const tasksApi = new TasksApi()
const configApi = new ConfigApi()
const { isMobile } = useBreakpoint()

function queryString(value: unknown): string {
  if (Array.isArray(value)) return queryString(value[0])
  return typeof value === 'string' ? value : ''
}

const sn = computed<number | null>(() => {
  const raw = queryString(route.query.sn)
  if (raw === '') return null
  const parsed = Number(raw)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
})

const titleFromQuery = computed(() => queryString(route.query.title))

const RESOLUTIONS: Resolution[] = ['1080', '720', '540', '480', '360']

interface FormState {
  resolution: Resolution
}

const form = reactive<FormState>({
  resolution: '1080',
})

const dialogVisible = ref(false)
const submitting = ref(false)

onMounted(async () => {
  if (sn.value === null) return
  dialogVisible.value = true
  try {
    const settings = await configApi.load()
    form.resolution = settings.download_resolution
  } catch {
    // Best-effort prefill — fall back to the '1080' default declared above.
  }
})

function buildRequest(): ManualTaskRequest {
  return {
    sn: String(sn.value as number),
    resolution: form.resolution,
    mode: 'single',
    thread: 1,
    classify: true,
    danmu: false,
  }
}

async function submit(): Promise<void> {
  if (sn.value === null) return
  submitting.value = true
  try {
    await tasksApi.submitManual(buildRequest())
    const label = titleFromQuery.value.trim() !== '' ? titleFromQuery.value.trim() : `sn=${sn.value}`
    ElMessage.success(`已加入下載佇列：《${label}》`)
    setTimeout(() => window.close(), 800)
  } catch (err) {
    ElMessage.error(`加入下載佇列失敗：${(err as Error).message}`)
  } finally {
    submitting.value = false
  }
}

function cancel(): void {
  dialogVisible.value = false
  window.close()
}
</script>

<template>
  <div class="ag-quickdl-container">
    <el-card
      v-if="sn === null"
      class="ag-quickdl-card"
    >
      <template #header>
        直接下載
      </template>
      <el-alert
        type="error"
        :closable="false"
      >
        <template #title>
          此頁面需要從動畫瘋透過擴充啟動
        </template>
      </el-alert>
    </el-card>

    <template v-else>
      <el-card class="ag-quickdl-card">
        <template #header>
          直接下載
        </template>
        <p class="ag-quickdl-hint">
          sn={{ sn }}
        </p>
      </el-card>

      <el-dialog
        v-model="dialogVisible"
        title="直接下載"
        :width="isMobile ? '100%' : '420px'"
        :fullscreen="isMobile"
        :close-on-click-modal="false"
        @close="cancel"
      >
        <el-form label-width="90px">
          <el-form-item label="sn">
            <el-input
              :model-value="String(sn)"
              readonly
            />
          </el-form-item>
          <el-form-item label="名稱">
            <el-input
              :model-value="titleFromQuery"
              readonly
            />
          </el-form-item>
          <el-form-item label="解析度">
            <el-select v-model="form.resolution">
              <el-option
                v-for="r in RESOLUTIONS"
                :key="r"
                :label="`${r}P`"
                :value="r"
              />
            </el-select>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button
            :disabled="submitting"
            @click="cancel"
          >
            取消
          </el-button>
          <el-button
            type="primary"
            :loading="submitting"
            :disabled="submitting"
            @click="submit"
          >
            下載
          </el-button>
        </template>
      </el-dialog>
    </template>
  </div>
</template>

<style scoped>
.ag-quickdl-container {
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 32px 16px;
}
.ag-quickdl-card {
  width: 100%;
  max-width: 420px;
}
.ag-quickdl-hint {
  color: #9ca3af;
  font-size: 13px;
  margin: 0;
}
</style>
