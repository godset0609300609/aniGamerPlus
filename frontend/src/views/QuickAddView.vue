<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { AnimeListApi } from '@/api/animelist'
import { useAuthStore } from '@/stores/auth'
import { useBreakpoint } from '@/composables/useBreakpoint'
import type { AnimeListEntry, AnimeListMode } from '@/types'

/**
 * QuickAddView — lightweight landing page opened as a popup by the
 * Tampermonkey userscript / bookmarklet injected on 動畫瘋 anime pages
 * (see BrowserExtensionDialog.vue for the injected snippets).
 *
 * The popup shares the app's existing session cookie (same origin), so no
 * cross-origin API call or CORS setup is needed — it simply reuses
 * AnimeListApi like AnimeListView.vue does.
 *
 * There is no dedicated "create one entry" endpoint on the backend: the
 * anime-list API only exposes GET (list) and PUT (replace the caller's
 * whole slice). We therefore fetch the current list, append the new entry,
 * and PUT the full array back — the same pattern AnimeListView.vue uses
 * for its own add/save flow.
 */

const route = useRoute()
const api = new AnimeListApi()
const { isAdmin, user } = useAuthStore()
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

const MODES: { value: AnimeListMode; label: string }[] = [
  { value: 'single', label: '僅本集 (single)' },
  { value: 'latest', label: '最後一集 (latest)' },
  { value: 'all', label: '全部劇集 (all)' },
  { value: 'largest-sn', label: '最近上傳 (largest-sn)' },
]

interface OwnerOption {
  ownerId: string
  ownerUsername: string
}

interface FormState {
  name: string
  tag: string
  season: number
  mode: AnimeListMode | null
  ownerId: string
}

const form = reactive<FormState>({
  name: '',
  tag: '',
  season: 1,
  mode: null,
  ownerId: '',
})

const dialogVisible = ref(false)
const submitting = ref(false)
const ownerOptions = ref<OwnerOption[]>([])

/**
 * Admin-only owner picker. There is no dedicated "list users" endpoint on
 * the backend, so the option set is derived from the distinct
 * (owner_id, owner_username) pairs already present in the currently
 * loaded anime-list entries — the same data AnimeListView.vue groups by.
 * The current admin is always included even if they have zero entries yet.
 */
async function loadOwnerOptions(): Promise<void> {
  const seen = new Map<string, string>()
  if (user.value) {
    seen.set(user.value.id, user.value.username)
  }
  try {
    const payload = await api.list()
    for (const entry of payload.entries ?? []) {
      if (entry.owner_id) {
        seen.set(entry.owner_id, entry.owner_username ?? entry.owner_id)
      }
    }
  } catch {
    // Best-effort — fall back to just the current admin as an option.
  }
  ownerOptions.value = Array.from(seen.entries()).map(([ownerId, ownerUsername]) => ({
    ownerId,
    ownerUsername,
  }))
}

onMounted(async () => {
  if (sn.value === null) return
  form.name = titleFromQuery.value
  form.ownerId = user.value?.id ?? ''
  dialogVisible.value = true
  if (isAdmin.value) {
    await loadOwnerOptions()
  }
})

function buildEntry(): AnimeListEntry {
  const trimmedName = form.name.trim()
  return {
    sn: sn.value as number,
    enabled: true,
    bilingual: false,
    mode: form.mode,
    tag: form.tag,
    season: form.season,
    custom_name: trimmedName === '' ? null : trimmedName,
    comment: '',
    anime_name: null,
    downloaded_episodes: 0,
    known_episodes: 0,
    owner_id: isAdmin.value ? (form.ownerId || null) : null,
    owner_username: null,
  }
}

async function submit(): Promise<void> {
  if (sn.value === null) return
  submitting.value = true
  try {
    const payload = await api.list()
    const entries = payload.entries ?? []
    await api.replaceAll([...entries, buildEntry()])
    const label = form.name.trim() !== '' ? form.name.trim() : `sn=${sn.value}`
    ElMessage.success(`已加入《${label}》`)
    setTimeout(() => window.close(), 800)
  } catch (err) {
    ElMessage.error(`加入追番清單失敗：${(err as Error).message}`)
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
  <div class="ag-quickadd-container">
    <el-card
      v-if="sn === null"
      class="ag-quickadd-card"
    >
      <template #header>
        加入追番清單
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
      <el-card class="ag-quickadd-card">
        <template #header>
          加入追番清單
        </template>
        <p class="ag-quickadd-hint">
          sn={{ sn }}
        </p>
      </el-card>

      <el-dialog
        v-model="dialogVisible"
        title="快速加入追番"
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
              v-model="form.name"
              placeholder="番劇名稱"
            />
          </el-form-item>
          <el-form-item label="群組">
            <el-input
              v-model="form.tag"
              placeholder="（可空）"
            />
          </el-form-item>
          <el-form-item label="季">
            <el-input-number
              v-model="form.season"
              :min="1"
            />
          </el-form-item>
          <el-form-item label="下載模式">
            <el-select
              v-model="form.mode"
              placeholder="使用預設"
              clearable
            >
              <el-option
                v-for="m in MODES"
                :key="m.value"
                :label="m.label"
                :value="m.value"
              />
            </el-select>
          </el-form-item>
          <el-form-item
            v-if="isAdmin"
            label="擁有者"
          >
            <el-select v-model="form.ownerId">
              <el-option
                v-for="o in ownerOptions"
                :key="o.ownerId"
                :label="o.ownerUsername"
                :value="o.ownerId"
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
            加入
          </el-button>
        </template>
      </el-dialog>
    </template>
  </div>
</template>

<style scoped>
.ag-quickadd-container {
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 32px 16px;
}
.ag-quickadd-card {
  width: 100%;
  max-width: 420px;
}
.ag-quickadd-hint {
  color: #9ca3af;
  font-size: 13px;
  margin: 0;
}
</style>
