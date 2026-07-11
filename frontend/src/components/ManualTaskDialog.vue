<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ConfigApi } from '@/api/config'
import { TasksApi, detectSource, extractSn } from '@/api/tasks'
import { useBreakpoint } from '@/composables/useBreakpoint'
import type { ManualDownloadMode, ManualTaskRequest, Resolution } from '@/types'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

const tasksApi = new TasksApi()
const configApi = new ConfigApi()
const { isMobile } = useBreakpoint()

interface FormState {
  link: string
  mode: ManualDownloadMode
  resolution: Resolution
  classify: boolean
  bilingual: boolean
  danmu: boolean
  thread: number
}

const form = reactive<FormState>({
  link: '',
  mode: 'single',
  resolution: '1080',
  classify: true,
  bilingual: false,
  danmu: false,
  thread: 1,
})

const MODES: { value: ManualDownloadMode; label: string }[] = [
  { value: 'single', label: '僅本集 (single)' },
  { value: 'latest', label: '最後一集 (latest)' },
  { value: 'all', label: '全部劇集 (all)' },
  { value: 'largest-sn', label: '最近上傳 (largest-sn)' },
]
const RESOLUTIONS: Resolution[] = ['1080', '720', '540', '480', '360']

const submitting = ref(false)

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const source = computed(() => detectSource(form.link))

watch(visible, async (open) => {
  if (!open) return
  try {
    const settings = await configApi.load()
    form.thread = settings['multi-thread']
  } catch {
    /* best-effort prefill */
  }
})

async function submit(): Promise<void> {
  const result = extractSn(form.link)
  if (!result) {
    ElMessage.error('請輸入合法的影片連結或 sn')
    return
  }
  submitting.value = true
  let request: ManualTaskRequest
  if (result.source === 'bilibili') {
    request = {
      sn: result.bvid,
      source: 'bilibili',
      resolution: form.resolution,
      mode: 'single',
      thread: 1,
      classify: form.classify,
      danmu: false,
    }
  } else {
    request = {
      sn: result.sn,
      resolution: form.resolution,
      mode: form.mode,
      thread: form.thread,
      classify: form.classify,
      danmu: form.danmu,
      bilingual: form.bilingual,
    }
  }
  try {
    await tasksApi.submitManual(request)
    ElMessage.success('手動任務已提交')
    visible.value = false
  } catch (err) {
    ElMessage.error(`任務提交失敗: ${(err as Error).message}`)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="手動新增任務"
    :width="isMobile ? '100%' : '640px'"
    :fullscreen="isMobile"
  >
    <el-form label-width="120px">
      <el-form-item label="動畫瘋或 Bilibili">
        <el-input
          v-model="form.link"
          placeholder="動畫瘋或 Bilibili 連結"
        />
        <div
          v-if="source === 'bilibili'"
          class="bilibili-hint"
        >
          偵測到 Bilibili 影片，將以單一影片模式下載。
        </div>
      </el-form-item>
      <el-form-item
        v-if="source === 'animad'"
        label="下載模式"
      >
        <el-select v-model="form.mode">
          <el-option
            v-for="m in MODES"
            :key="m.value"
            :label="m.label"
            :value="m.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="下載解析度">
        <el-select v-model="form.resolution">
          <el-option
            v-for="r in RESOLUTIONS"
            :key="r"
            :label="`${r}P`"
            :value="r"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="建立番劇資料夾">
        <el-switch v-model="form.classify" />
      </el-form-item>
      <el-form-item
        v-if="source === 'animad'"
        label="雙語"
      >
        <el-tooltip
          content="同時抓日文原音與中文配音（中文配音會加上 [中] 檔名標記）"
          placement="top"
        >
          <el-switch v-model="form.bilingual" />
        </el-tooltip>
      </el-form-item>
      <el-form-item
        v-if="source === 'animad'"
        label="下載彈幕"
      >
        <el-switch v-model="form.danmu" />
      </el-form-item>
      <el-form-item label="最大同時下載數">
        <el-input-number
          v-model="form.thread"
          :min="1"
          :max="50"
          :disabled="source === 'bilibili'"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button
        :disabled="submitting"
        @click="visible = false"
      >
        關閉
      </el-button>
      <el-button
        type="success"
        :loading="submitting"
        :disabled="submitting"
        @click="submit"
      >
        提交
      </el-button>
    </template>
  </el-dialog>
</template>
