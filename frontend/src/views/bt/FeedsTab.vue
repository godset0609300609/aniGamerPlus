<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { BtApi, type BtFeedCreate } from '@/api/bt'
import { useAutoRefresh } from '@/composables/useAutoRefresh'
import { useBreakpoint } from '@/composables/useBreakpoint'
import type { BtFeed, BtProbeResult } from '@/types'

const api = new BtApi()
const { isMobile } = useBreakpoint()

const feeds = ref<BtFeed[]>([])
const loading = ref(false)

async function loadFeeds(): Promise<void> {
  loading.value = true
  try {
    feeds.value = await api.listFeeds()
  } catch (err) {
    ElMessage.error(`讀取 RSS 來源失敗：${(err as Error).message}`)
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------------------
// Wizard dialog state
// ---------------------------------------------------------------------------

type WizardMode = 'create' | 'edit'

const dialogVisible = ref(false)
const wizardMode = ref<WizardMode>('create')
const editingId = ref<number | null>(null)
/** 1 = paste URL, 2 = pick fields, 3 = name + save */
const step = ref(1)

const urlDraft = ref('')
const probing = ref(false)
const probeError = ref<string | null>(null)
const probeResult = ref<BtProbeResult | null>(null)

const titleKey = ref('')
const linkKey = ref('')
const guidKey = ref<string | null>(null)
const authorKey = ref<string | null>(null)

const nameDraft = ref('')
const enabledDraft = ref(true)
const savingFeed = ref(false)

function resetWizard(): void {
  wizardMode.value = 'create'
  editingId.value = null
  step.value = 1
  urlDraft.value = ''
  probing.value = false
  probeError.value = null
  probeResult.value = null
  titleKey.value = ''
  linkKey.value = ''
  guidKey.value = null
  authorKey.value = null
  nameDraft.value = ''
  enabledDraft.value = true
  savingFeed.value = false
}

function openCreateDialog(): void {
  resetWizard()
  dialogVisible.value = true
}

async function openEditDialog(feed: BtFeed): Promise<void> {
  resetWizard()
  wizardMode.value = 'edit'
  editingId.value = feed.id
  urlDraft.value = feed.url
  titleKey.value = feed.title_key
  linkKey.value = feed.link_key
  guidKey.value = feed.guid_key
  authorKey.value = feed.author_key
  nameDraft.value = feed.name
  enabledDraft.value = feed.enabled
  step.value = 2
  dialogVisible.value = true

  probing.value = true
  try {
    probeResult.value = await api.probeFeed(feed.url)
  } catch (err) {
    probeError.value = (err as Error).message
  } finally {
    probing.value = false
  }
}

async function testProbe(): Promise<void> {
  probeError.value = null
  probing.value = true
  try {
    probeResult.value = await api.probeFeed(urlDraft.value)
    step.value = 2
  } catch (err) {
    probeError.value = (err as Error).message
  } finally {
    probing.value = false
  }
}

const canProceedToNaming = computed(() => titleKey.value !== '' && linkKey.value !== '')

function goToNaming(): void {
  if (!canProceedToNaming.value) return
  step.value = 3
}

function backToFields(): void {
  step.value = 2
}

async function submitFeed(): Promise<void> {
  savingFeed.value = true
  try {
    if (wizardMode.value === 'create') {
      const body: BtFeedCreate = {
        name: nameDraft.value,
        url: urlDraft.value,
        title_key: titleKey.value,
        link_key: linkKey.value,
        guid_key: guidKey.value || null,
        author_key: authorKey.value || null,
        enabled: enabledDraft.value,
      }
      await api.createFeed(body)
    } else if (editingId.value !== null) {
      await api.updateFeed(editingId.value, {
        name: nameDraft.value,
        title_key: titleKey.value,
        link_key: linkKey.value,
        guid_key: guidKey.value || null,
        author_key: authorKey.value || null,
        enabled: enabledDraft.value,
      })
    }
    ElMessage.success('RSS 來源已儲存')
    dialogVisible.value = false
    await loadFeeds()
  } catch (err) {
    ElMessage.error(`儲存失敗：${(err as Error).message}`)
  } finally {
    savingFeed.value = false
  }
}

async function toggleEnabled(row: BtFeed, value: boolean): Promise<void> {
  const previous = row.enabled
  row.enabled = value
  try {
    await api.updateFeed(row.id, { enabled: value })
  } catch (err) {
    row.enabled = previous
    ElMessage.error(`更新失敗：${(err as Error).message}`)
  }
}

async function removeFeed(row: BtFeed): Promise<void> {
  try {
    await ElMessageBox.confirm(`確定要刪除「${row.name}」這個 RSS 來源嗎？`, '刪除 RSS 來源', {
      confirmButtonText: '確定',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await api.deleteFeed(row.id)
    ElMessage.success('已刪除')
    await loadFeeds()
  } catch (err) {
    ElMessage.error(`刪除失敗：${(err as Error).message}`)
  }
}

// ---------------------------------------------------------------------------
// Sample preview — resolves dotted key paths against raw sample entries.
// ---------------------------------------------------------------------------

function resolvePath(entry: Record<string, unknown>, path: string): string {
  if (!path) return ''
  const value = path
    .split('.')
    .reduce<unknown>((acc, part) => {
      if (acc === null || acc === undefined || typeof acc !== 'object') return undefined
      return (acc as Record<string, unknown>)[part]
    }, entry)
  if (value === null || value === undefined) return ''
  return String(value)
}

interface PreviewRow {
  idx: number
  title: string
  link: string
  guid: string
  author: string
}

const previewEntries = computed<PreviewRow[]>(() => {
  const samples = probeResult.value?.sample_entries ?? []
  return samples.slice(0, 5).map((entry, i) => ({
    idx: i + 1,
    title: resolvePath(entry, titleKey.value),
    link: resolvePath(entry, linkKey.value),
    guid: guidKey.value ? resolvePath(entry, guidKey.value) : '',
    author: authorKey.value ? resolvePath(entry, authorKey.value) : '',
  }))
})

function mappingSummary(feed: BtFeed): string {
  return `title=${feed.title_key}, link=${feed.link_key}, guid=${feed.guid_key ?? feed.link_key}`
}

onMounted(loadFeeds)

// Fix 5 — live-refresh so entry_count updates as new items are collected
// while a user is parked on this tab. The wizard dialog holds its own
// local draft state (urlDraft/titleKey/... ) untouched by loadFeeds, so no
// dirty-guard is needed here unlike FiltersTab.
useAutoRefresh(5000, loadFeeds)
</script>

<template>
  <div class="ag-bt-feeds">
    <div class="ag-toolbar">
      <el-button
        type="primary"
        @click="openCreateDialog"
      >
        新增 RSS 來源
      </el-button>
    </div>

    <!-- Horizontal-scroll wrapper: 收集數 is hidden below the mobile
         breakpoint (lowest-priority column), but the remaining columns
         (name/URL/mapping/actions) still need more room than a 375px
         viewport gives, so the table scrolls within this container. -->
    <div class="ag-table-scroll">
      <el-table
        :data="feeds"
        stripe
        size="small"
        class="ag-feeds-table"
        empty-text=" "
      >
        <el-table-column
          label="啟用"
          width="70"
        >
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled"
              @update:model-value="(val: string | number | boolean) => toggleEnabled(row, val as boolean)"
            />
          </template>
        </el-table-column>
        <el-table-column
          label="名稱"
          min-width="140"
        >
          <template #default="{ row }">
            {{ row.name }}
          </template>
        </el-table-column>
        <el-table-column
          label="URL"
          min-width="220"
        >
          <template #default="{ row }">
            <el-tooltip
              :content="row.url"
              placement="top"
            >
              <span class="ag-feed-url">{{ row.url }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column
          label="欄位映射"
          min-width="220"
        >
          <template #default="{ row }">
            <el-tooltip
              :content="mappingSummary(row)"
              placement="top"
            >
              <span>{{ mappingSummary(row) }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column
          v-if="!isMobile"
          label="收集數"
          width="100"
          align="right"
        >
          <template #default="{ row }">
            <el-tag
              v-if="row.entry_count > 0"
              type="info"
              size="small"
            >
              {{ row.entry_count }}
            </el-tag>
            <span v-else>{{ row.entry_count }}</span>
          </template>
        </el-table-column>
        <el-table-column
          label="操作"
          width="140"
        >
          <template #default="{ row }">
            <el-button
              size="small"
              link
              @click="openEditDialog(row)"
            >
              編輯
            </el-button>
            <el-button
              size="small"
              type="danger"
              link
              @click="removeFeed(row)"
            >
              刪除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div
      v-if="!loading && feeds.length === 0"
      class="ag-empty"
    >
      目前沒有任何 RSS 來源，點擊上方「新增 RSS 來源」開始設定。
    </div>

    <!-- ================= Wizard dialog ================= -->
    <el-dialog
      v-model="dialogVisible"
      :title="wizardMode === 'create' ? '新增 RSS 來源' : '編輯 RSS 來源'"
      :width="isMobile ? '100%' : '640px'"
      :fullscreen="isMobile"
    >
      <el-steps
        :active="step - 1"
        finish-status="success"
        simple
        class="ag-wizard-steps"
      >
        <el-step title="貼上網址" />
        <el-step title="選欄位" />
        <el-step title="命名 + 儲存" />
      </el-steps>

      <!-- Step 1: paste URL -->
      <div
        v-if="step === 1"
        class="ag-wizard-step"
      >
        <el-form-item label="RSS 網址">
          <el-input
            v-model="urlDraft"
            placeholder="https://example.com/rss.xml"
          />
        </el-form-item>
        <el-alert
          v-if="probeError"
          type="error"
          :closable="false"
          class="ag-probe-error"
        >
          <template #title>
            測試失敗：{{ probeError }}
          </template>
        </el-alert>
        <el-button
          type="primary"
          :loading="probing"
          :disabled="!urlDraft"
          @click="testProbe"
        >
          測試
        </el-button>
      </div>

      <!-- Step 2: pick fields -->
      <div
        v-else-if="step === 2"
        class="ag-wizard-step"
      >
        <el-alert
          v-if="probeError"
          type="error"
          :closable="false"
          class="ag-probe-error"
        >
          <template #title>
            無法重新取得欄位：{{ probeError }}（可沿用既有映射）
          </template>
        </el-alert>

        <el-form-item label="標題欄位 (title_key)">
          <el-select
            v-model="titleKey"
            placeholder="選擇欄位"
          >
            <el-option
              v-for="k in probeResult?.available_keys ?? []"
              :key="k"
              :label="k"
              :value="k"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="連結欄位 (link_key)">
          <el-select
            v-model="linkKey"
            placeholder="選擇欄位"
          >
            <el-option
              v-for="k in probeResult?.available_keys ?? []"
              :key="k"
              :label="k"
              :value="k"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="唯一識別碼 (guid_key)">
          <el-select
            v-model="guidKey"
            placeholder="留空則使用連結欄位"
            clearable
          >
            <el-option
              v-for="k in probeResult?.available_keys ?? []"
              :key="k"
              :label="k"
              :value="k"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="作者欄位 (author_key)">
          <el-select
            v-model="authorKey"
            placeholder="（選填）"
            clearable
          >
            <el-option
              v-for="k in probeResult?.available_keys ?? []"
              :key="k"
              :label="k"
              :value="k"
            />
          </el-select>
        </el-form-item>

        <div class="ag-preview-list">
          <div class="ag-preview-title">
            預覽（前 {{ previewEntries.length }} 筆）
          </div>
          <el-table
            :data="previewEntries"
            size="small"
            max-height="40vh"
            class="ag-preview-table"
          >
            <el-table-column
              label="#"
              width="40"
            >
              <template #default="{ row }">
                {{ row.idx }}
              </template>
            </el-table-column>
            <el-table-column
              label="標題"
              min-width="160"
              :show-overflow-tooltip="{ popperClass: 'ag-wide-tooltip' }"
            >
              <template #default="{ row }">
                {{ row.title || '（未取得）' }}
              </template>
            </el-table-column>
            <el-table-column
              label="連結"
              min-width="200"
              :show-overflow-tooltip="{ popperClass: 'ag-wide-tooltip' }"
            >
              <template #default="{ row }">
                {{ row.link || '（未取得）' }}
              </template>
            </el-table-column>
            <el-table-column
              v-if="guidKey"
              label="唯一識別碼"
              min-width="160"
              :show-overflow-tooltip="{ popperClass: 'ag-wide-tooltip' }"
            >
              <template #default="{ row }">
                {{ row.guid || '（未取得）' }}
              </template>
            </el-table-column>
            <el-table-column
              v-if="authorKey"
              label="作者"
              min-width="120"
              :show-overflow-tooltip="{ popperClass: 'ag-wide-tooltip' }"
            >
              <template #default="{ row }">
                {{ row.author || '（未取得）' }}
              </template>
            </el-table-column>
          </el-table>
        </div>

        <div class="ag-wizard-actions">
          <el-button @click="step = 1">
            上一步
          </el-button>
          <el-button
            type="primary"
            :disabled="!canProceedToNaming"
            @click="goToNaming"
          >
            下一步
          </el-button>
        </div>
      </div>

      <!-- Step 3: name + save -->
      <div
        v-else
        class="ag-wizard-step"
      >
        <el-form-item label="名稱">
          <el-input
            v-model="nameDraft"
            placeholder="例如：dmhy 動畫"
          />
        </el-form-item>
        <el-form-item label="啟用">
          <el-switch v-model="enabledDraft" />
        </el-form-item>

        <div class="ag-wizard-actions">
          <el-button @click="backToFields">
            上一步
          </el-button>
          <el-button
            type="primary"
            :loading="savingFeed"
            :disabled="!nameDraft"
            @click="submitFeed"
          >
            儲存
          </el-button>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.ag-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.ag-table-scroll {
  width: 100%;
  overflow-x: auto;
}
.ag-feeds-table {
  width: 100%;
}
.ag-feed-url {
  word-break: break-all;
}
/* Uniform row height — URL/mapping text vary wildly in length, which
   otherwise makes the table look ragged. */
.ag-feeds-table :deep(.el-table__row) {
  height: 56px;
}
.ag-feeds-table :deep(.el-table__row td) {
  vertical-align: middle;
}
.ag-feeds-table :deep(.el-table__row td .cell) {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ag-empty {
  text-align: center;
  color: #9ca3af;
  padding: 32px 0;
}
.ag-wizard-steps {
  margin-bottom: 20px;
}
.ag-wizard-step {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ag-probe-error {
  margin-bottom: 8px;
}
.ag-wizard-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}
.ag-preview-list {
  margin-top: 8px;
  border: 1px solid var(--el-border-color, #e4e7ed);
  border-radius: 4px;
  padding: 8px;
}
.ag-preview-title {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}
.ag-preview-table {
  width: 100%;
}
</style>

<style>
.ag-wide-tooltip.el-popper {
  max-width: 600px;
  word-break: break-all;
  white-space: normal;
  line-height: 1.5;
}
</style>
