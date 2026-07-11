<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { TgApi, type TgWatchedChatCreate } from '@/api/tg'
import { useAutoRefresh } from '@/composables/useAutoRefresh'
import { useBreakpoint } from '@/composables/useBreakpoint'
import type { TgAvailableChat, TgWatchedChat } from '@/types'

const { isMobile } = useBreakpoint()

const MEDIA_TYPE_OPTIONS = [
  { label: '影片', value: 'video' },
  { label: '文件', value: 'document' },
  { label: '音訊', value: 'audio' },
  { label: '照片', value: 'photo' },
]

const api = new TgApi()

const chats = ref<TgWatchedChat[]>([])
const loading = ref(false)

async function loadChats(): Promise<void> {
  loading.value = true
  try {
    chats.value = await api.listChats()
  } catch (err) {
    ElMessage.error(`讀取監控清單失敗：${(err as Error).message}`)
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------------------
// Chat picker dialog — "新增監控 Chat"
// ---------------------------------------------------------------------------

const pickerVisible = ref(false)
const pickerLoading = ref(false)
const pickerError = ref<string | null>(null)
const availableChats = ref<TgAvailableChat[]>([])

// 回填歷史檔案 — applied to whichever chat is picked next.
const pickerBackfillEnabled = ref(false)
const pickerBackfillDays = ref(7)

// ---------------------------------------------------------------------------
// Picker search + category filter
// ---------------------------------------------------------------------------

const PICKER_SEARCH_DEBOUNCE_MS = 200

interface CategoryOption {
  label: string
  value: string
}

// NOTE: 群組 matches both pyrogram's 'group' (basic/legacy) and 'supergroup'
// chat types — Telegram users don't distinguish between the two, so folding
// them into one category avoids basic groups silently vanishing from the
// filter.
const CATEGORY_OPTIONS: CategoryOption[] = [
  { label: '全部', value: '' },
  { label: 'Bot', value: 'bot' },
  { label: '私人', value: 'private' },
  { label: '群組', value: 'group' },
  { label: '頻道', value: 'channel' },
]

const pickerSearchInput = ref('')
const pickerSearchQuery = ref('')
const pickerCategoryFilter = ref('')

let pickerSearchTimer: ReturnType<typeof setTimeout> | null = null

watch(pickerSearchInput, (value) => {
  if (pickerSearchTimer !== null) {
    clearTimeout(pickerSearchTimer)
    pickerSearchTimer = null
  }
  pickerSearchTimer = setTimeout(() => {
    pickerSearchQuery.value = value
  }, PICKER_SEARCH_DEBOUNCE_MS)
})

function matchesCategory(chat: TgAvailableChat, category: string): boolean {
  if (!category) return true
  if (category === 'group') return chat.type === 'group' || chat.type === 'supergroup'
  return chat.type === category
}

const filteredAvailableChats = computed(() => {
  const query = pickerSearchQuery.value.trim().toLowerCase()
  return availableChats.value.filter((chat) => {
    if (!matchesCategory(chat, pickerCategoryFilter.value)) return false
    if (query && !chat.title.toLowerCase().includes(query)) return false
    return true
  })
})

function resetPickerFilters(): void {
  if (pickerSearchTimer !== null) {
    clearTimeout(pickerSearchTimer)
    pickerSearchTimer = null
  }
  pickerSearchInput.value = ''
  pickerSearchQuery.value = ''
  pickerCategoryFilter.value = ''
  pickerBackfillEnabled.value = false
  pickerBackfillDays.value = 7
}

onUnmounted(() => {
  if (pickerSearchTimer !== null) clearTimeout(pickerSearchTimer)
})

async function openPicker(): Promise<void> {
  pickerVisible.value = true
  pickerError.value = null
  pickerLoading.value = true
  resetPickerFilters()
  try {
    availableChats.value = await api.listAvailableChats()
  } catch (err) {
    pickerError.value = (err as Error).message
  } finally {
    pickerLoading.value = false
  }
}

async function pickChat(chat: TgAvailableChat): Promise<void> {
  const body: TgWatchedChatCreate = {
    chat_id: chat.chat_id,
    chat_title: chat.title,
    media_types: ['video'],
    size_min_mb: null,
    size_max_mb: null,
    format_whitelist: null,
    save_path: null,
    enabled: true,
    backfill_enabled: pickerBackfillEnabled.value,
    backfill_days: pickerBackfillDays.value,
  }
  try {
    const created = await api.createChat(body)
    ElMessage.success('已加入監控')
    pickerVisible.value = false
    await loadChats()
    openEditDialog(created)
  } catch (err) {
    ElMessage.error(`加入監控失敗：${(err as Error).message}`)
  }
}

// ---------------------------------------------------------------------------
// Edit dialog — media types / size range / formats / save path
// ---------------------------------------------------------------------------

const editDialogVisible = ref(false)
const editingChat = ref<TgWatchedChat | null>(null)
const editMediaTypes = ref<string[]>([])
const editSizeMin = ref<number | null>(null)
const editSizeMax = ref<number | null>(null)
const editFormats = ref('')
const editSavePath = ref('')
const editEnabled = ref(true)
const editBackfillEnabled = ref(false)
const editBackfillDays = ref(7)
const saving = ref(false)
const retryingBackfill = ref(false)

function openEditDialog(chat: TgWatchedChat): void {
  editingChat.value = chat
  editMediaTypes.value = [...chat.media_types]
  editSizeMin.value = chat.size_min_mb
  editSizeMax.value = chat.size_max_mb
  editFormats.value = chat.format_whitelist?.join(', ') ?? ''
  editSavePath.value = chat.save_path ?? ''
  editEnabled.value = chat.enabled
  editBackfillEnabled.value = chat.backfill_enabled
  editBackfillDays.value = chat.backfill_days
  editDialogVisible.value = true
}

function parseFormats(raw: string): string[] | null {
  const parts = raw
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
  return parts.length > 0 ? parts : null
}

async function saveEdit(): Promise<void> {
  if (!editingChat.value) return
  saving.value = true
  try {
    await api.updateChat(editingChat.value.id, {
      media_types: editMediaTypes.value,
      size_min_mb: editSizeMin.value,
      size_max_mb: editSizeMax.value,
      format_whitelist: parseFormats(editFormats.value),
      save_path: editSavePath.value.trim() || null,
      enabled: editEnabled.value,
      backfill_enabled: editBackfillEnabled.value,
      backfill_days: editBackfillDays.value,
    })
    ElMessage.success('已儲存')
    editDialogVisible.value = false
    await loadChats()
  } catch (err) {
    ElMessage.error(`儲存失敗：${(err as Error).message}`)
  } finally {
    saving.value = false
  }
}

// ---------------------------------------------------------------------------
// Backfill retry / re-run
// ---------------------------------------------------------------------------

async function retryBackfill(row: TgWatchedChat, confirmRerun = false): Promise<void> {
  if (confirmRerun) {
    try {
      await ElMessageBox.confirm(
        '已完成過，重新回填會重新掃描並依 UNIQUE 去重，只會下載目前 DB 沒有紀錄的訊息。確定要重新回填嗎？',
        '重新回填',
        { confirmButtonText: '確定', cancelButtonText: '取消', type: 'warning' },
      )
    } catch {
      return
    }
  }
  retryingBackfill.value = true
  try {
    const updated = await api.retryBackfill(row.id)
    ElMessage.success('已加入回填佇列')
    if (editingChat.value?.id === row.id) editingChat.value = updated
    await loadChats()
  } catch (err) {
    ElMessage.error(`回填失敗：${(err as Error).message}`)
  } finally {
    retryingBackfill.value = false
  }
}

// ---------------------------------------------------------------------------
// Enable toggle / delete
// ---------------------------------------------------------------------------

async function toggleEnabled(row: TgWatchedChat, value: boolean): Promise<void> {
  const previous = row.enabled
  row.enabled = value
  try {
    await api.updateChat(row.id, { enabled: value })
  } catch (err) {
    row.enabled = previous
    ElMessage.error(`更新失敗：${(err as Error).message}`)
  }
}

async function removeChat(row: TgWatchedChat): Promise<void> {
  try {
    await ElMessageBox.confirm(`確定要移除「${row.chat_title}」的監控嗎？`, '移除監控', {
      confirmButtonText: '確定',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await api.deleteChat(row.id)
    ElMessage.success('已移除')
    await loadChats()
  } catch (err) {
    ElMessage.error(`移除失敗：${(err as Error).message}`)
  }
}

function mediaTypesSummary(row: TgWatchedChat): string {
  return row.media_types
    .map((t) => MEDIA_TYPE_OPTIONS.find((o) => o.value === t)?.label ?? t)
    .join('、')
}

// ---------------------------------------------------------------------------
// Backfill status display (table column + edit dialog)
// ---------------------------------------------------------------------------

function backfillLabel(row: TgWatchedChat): string {
  switch (row.backfill_status) {
    case 'pending':
      return '回填排隊中...'
    case 'running':
      return row.backfill_scanned_count > 0
        ? `回填中 ${row.backfill_matched_count}/${row.backfill_scanned_count}`
        : '回填中...'
    case 'done':
      return `回填完成，抓到 ${row.backfill_matched_count} 個檔案`
    case 'failed':
      return '回填失敗'
    default:
      return ''
  }
}

function backfillTagType(row: TgWatchedChat): 'success' | 'danger' | 'info' | 'warning' {
  switch (row.backfill_status) {
    case 'done':
      return 'success'
    case 'failed':
      return 'danger'
    case 'running':
      return 'warning'
    default:
      return 'info'
  }
}

onMounted(loadChats)

// Fix 5 — live-refresh the watched-chat list. The "新增監控 Chat" picker
// dialog keeps its own `availableChats`/edit-draft state untouched by
// loadChats(), so no dirty-guard is needed here.
useAutoRefresh(5000, loadChats)
</script>

<template>
  <div class="ag-tg-chats">
    <div class="ag-toolbar">
      <el-button
        type="primary"
        @click="openPicker"
      >
        新增監控 Chat
      </el-button>
    </div>

    <div class="ag-table-scroll">
      <el-table
        :data="chats"
        stripe
        size="small"
        class="ag-chats-table"
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
          label="Chat"
          min-width="180"
        >
          <template #default="{ row }">
            {{ row.chat_title }}
          </template>
        </el-table-column>
        <el-table-column
          label="媒體類型"
          min-width="160"
        >
          <template #default="{ row }">
            {{ mediaTypesSummary(row) }}
          </template>
        </el-table-column>
        <el-table-column
          v-if="!isMobile"
          label="大小限制"
          width="140"
        >
          <template #default="{ row }">
            <span v-if="row.size_min_mb || row.size_max_mb">
              {{ row.size_min_mb ?? 0 }}–{{ row.size_max_mb ?? '∞' }} MB
            </span>
            <span
              v-else
              class="ag-muted"
            >不限</span>
          </template>
        </el-table-column>
        <el-table-column
          v-if="!isMobile"
          label="回填"
          width="180"
        >
          <template #default="{ row }">
            <el-tag
              v-if="row.backfill_status"
              size="small"
              :type="backfillTagType(row)"
            >
              {{ backfillLabel(row) }}
            </el-tag>
            <span
              v-else
              class="ag-muted"
            >—</span>
          </template>
        </el-table-column>
        <el-table-column
          label="操作"
          width="180"
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
              v-if="row.backfill_status === 'failed'"
              size="small"
              link
              type="warning"
              :loading="retryingBackfill"
              @click="retryBackfill(row)"
            >
              重試回填
            </el-button>
            <el-button
              size="small"
              type="danger"
              link
              @click="removeChat(row)"
            >
              移除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div
      v-if="!loading && chats.length === 0"
      class="ag-empty"
    >
      目前沒有監控中的 Chat，點擊上方「新增監控 Chat」開始設定。
    </div>

    <!-- ================= Chat picker dialog ================= -->
    <el-dialog
      v-model="pickerVisible"
      title="選擇要監控的 Chat"
      :width="isMobile ? '100%' : '520px'"
      :fullscreen="isMobile"
    >
      <el-alert
        v-if="pickerError"
        type="error"
        :closable="false"
        class="ag-picker-error"
      >
        <template #title>
          讀取失敗：{{ pickerError }}
        </template>
      </el-alert>

      <div
        v-if="!pickerLoading && !pickerError && availableChats.length > 0"
        class="ag-picker-backfill"
      >
        <el-checkbox
          v-model="pickerBackfillEnabled"
          class="ag-picker-backfill-checkbox"
        >
          回填歷史檔案
        </el-checkbox>
        <el-input-number
          v-model="pickerBackfillDays"
          :min="1"
          :max="90"
          :disabled="!pickerBackfillEnabled"
          size="small"
          controls-position="right"
          class="ag-picker-backfill-days"
        />
        <span class="ag-muted">天</span>
      </div>

      <div
        v-if="!pickerLoading && !pickerError && availableChats.length > 0"
        class="ag-picker-filters"
        :class="{ 'ag-picker-filters--mobile': isMobile }"
      >
        <el-input
          v-model="pickerSearchInput"
          placeholder="搜尋 Chat 名稱"
          clearable
          class="ag-picker-search"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <el-select
          v-model="pickerCategoryFilter"
          class="ag-picker-category"
        >
          <el-option
            v-for="opt in CATEGORY_OPTIONS"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </div>

      <div class="ag-picker-list">
        <div
          v-if="pickerLoading"
          class="ag-empty"
        >
          讀取中...
        </div>
        <div
          v-for="chat in filteredAvailableChats"
          :key="chat.chat_id"
          class="ag-picker-item"
          :class="{ 'ag-picker-item--disabled': chat.already_watched }"
          @click="!chat.already_watched && pickChat(chat)"
        >
          <span class="ag-picker-item__title">{{ chat.title }}</span>
          <el-tag
            size="small"
            type="info"
          >
            {{ chat.type }}
          </el-tag>
          <span
            v-if="chat.already_watched"
            class="ag-muted"
          >已監控</span>
        </div>
        <div
          v-if="!pickerLoading && availableChats.length === 0 && !pickerError"
          class="ag-empty"
        >
          沒有找到任何 Chat — 請確認已綁定 Telegram 帳號，且該帳號已加入至少一個聊天群組/頻道。
        </div>
        <el-empty
          v-else-if="!pickerLoading && availableChats.length > 0 && filteredAvailableChats.length === 0"
          description="沒有符合條件的 Chat"
        />
      </div>
    </el-dialog>

    <!-- ================= Edit dialog ================= -->
    <el-dialog
      v-model="editDialogVisible"
      :title="`編輯監控設定 — ${editingChat?.chat_title ?? ''}`"
      :width="isMobile ? '100%' : '520px'"
      :fullscreen="isMobile"
    >
      <el-form-item label="媒體類型">
        <el-checkbox-group v-model="editMediaTypes">
          <el-checkbox
            v-for="opt in MEDIA_TYPE_OPTIONS"
            :key="opt.value"
            :value="opt.value"
          >
            {{ opt.label }}
          </el-checkbox>
        </el-checkbox-group>
      </el-form-item>
      <el-row :gutter="12">
        <el-col :span="12">
          <el-form-item label="最小 (MB)">
            <el-input-number
              v-model="editSizeMin"
              :min="0"
              controls-position="right"
              class="ag-full-width"
            />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="最大 (MB)">
            <el-input-number
              v-model="editSizeMax"
              :min="0"
              controls-position="right"
              class="ag-full-width"
            />
          </el-form-item>
        </el-col>
      </el-row>
      <el-form-item label="副檔名白名單">
        <el-input
          v-model="editFormats"
          placeholder="例如：mp4, mkv（留空 = 不限）"
        />
      </el-form-item>
      <el-form-item label="存放路徑">
        <el-input
          v-model="editSavePath"
          placeholder="留空則使用預設路徑"
        />
      </el-form-item>
      <el-form-item label="啟用">
        <el-switch v-model="editEnabled" />
      </el-form-item>
      <el-form-item label="回填歷史檔案">
        <el-checkbox
          v-model="editBackfillEnabled"
          class="ag-edit-backfill-checkbox"
        >
          回填歷史檔案
        </el-checkbox>
        <el-input-number
          v-model="editBackfillDays"
          :min="1"
          :max="90"
          :disabled="!editBackfillEnabled"
          size="small"
          controls-position="right"
          class="ag-backfill-days ag-edit-backfill-days"
        />
        <span class="ag-muted">天</span>
      </el-form-item>
      <el-form-item
        v-if="editingChat && editingChat.backfill_status"
        label="回填狀態"
      >
        <el-tag
          size="small"
          :type="backfillTagType(editingChat)"
        >
          {{ backfillLabel(editingChat) }}
        </el-tag>
        <el-button
          v-if="editingChat.backfill_status === 'failed'"
          size="small"
          link
          type="warning"
          :loading="retryingBackfill"
          @click="retryBackfill(editingChat)"
        >
          重試回填
        </el-button>
        <el-button
          v-else-if="editingChat.backfill_status === 'done'"
          size="small"
          link
          :loading="retryingBackfill"
          @click="retryBackfill(editingChat, true)"
        >
          重新回填
        </el-button>
      </el-form-item>

      <div class="ag-dialog-actions">
        <el-button @click="editDialogVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          @click="saveEdit"
        >
          儲存
        </el-button>
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
.ag-chats-table {
  width: 100%;
}
.ag-muted {
  color: #9ca3af;
}
.ag-empty {
  text-align: center;
  color: #9ca3af;
  padding: 32px 0;
}
.ag-picker-error {
  margin-bottom: 8px;
}
.ag-picker-backfill {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.ag-backfill-days {
  margin: 0 8px;
}
.ag-picker-filters {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.ag-picker-search {
  flex: 1;
  min-width: 0;
}
.ag-picker-category {
  flex-shrink: 0;
  width: 120px;
}
.ag-picker-filters--mobile {
  flex-direction: column;
  align-items: stretch;
}
.ag-picker-filters--mobile .ag-picker-category {
  width: 100%;
}
.ag-picker-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 50vh;
  overflow-y: auto;
}
.ag-picker-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
}
.ag-picker-item:hover {
  background: var(--el-fill-color-light);
}
.ag-picker-item--disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.ag-picker-item__title {
  flex: 1;
  word-break: break-word;
}
.ag-full-width {
  width: 100%;
}
.ag-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}
</style>
