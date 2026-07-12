<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { BtApi } from '@/api/bt'
import { formatRelativeBare } from '@/utils/format'
import { tokenizeTitle } from '@/utils/tokenize'
import { useBreakpoint } from '@/composables/useBreakpoint'
import type { BtFeedEntry, BtFilter } from '@/types'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    nextSortOrder?: number
    initialEntry?: BtFeedEntry | null
    mode?: 'append-to-draft' | 'save-immediately'
  }>(),
  { nextSortOrder: 0, initialEntry: null, mode: 'append-to-draft' },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'filter-created', filter: BtFilter): void
}>()

const api = new BtApi()
const { isMobile } = useBreakpoint()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

// ---------------------------------------------------------------------------
// Section 1 — search
// ---------------------------------------------------------------------------

interface SuggestionItem {
  value: string
  entry: BtFeedEntry
}

const searchQuery = ref('')
const feedNames = ref<Record<number, string>>({})
let searchTimer: ReturnType<typeof setTimeout> | null = null

function entryOf(item: Record<string, unknown>): BtFeedEntry {
  return (item as unknown as SuggestionItem).entry
}

function fetchSuggestions(
  queryString: string,
  callback: (results: SuggestionItem[]) => void,
): void {
  if (searchTimer !== null) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  const query = queryString.trim()
  if (!query) {
    callback([])
    return
  }
  searchTimer = setTimeout(() => {
    api
      .searchEntries(query, 20)
      .then((results) => callback(results.map((entry) => ({ value: entry.title, entry }))))
      .catch((err: Error) => {
        ElMessage.error(`搜尋失敗：${err.message}`)
        callback([])
      })
  }, 250)
}

function feedName(feedId: number): string {
  return feedNames.value[feedId] ?? `#${feedId}`
}

async function loadFeeds(): Promise<void> {
  try {
    const feeds = await api.listFeeds()
    feedNames.value = Object.fromEntries(feeds.map((f) => [f.id, f.name]))
  } catch {
    // Best-effort — suggestions still work without a feed-name lookup.
  }
}

// ---------------------------------------------------------------------------
// Section 2 — tokens
// ---------------------------------------------------------------------------

const selectedEntry = ref<BtFeedEntry | null>(null)
const bracketTokens = ref<string[]>([])
const freeTextTokens = ref<string[]>([])
const manualTokens = ref<string[]>([])
const manualInput = ref('')

function selectEntry(entry: BtFeedEntry): void {
  selectedEntry.value = entry
  searchQuery.value = entry.title
  const tokens = tokenizeTitle(entry.title)
  bracketTokens.value = [...tokens.bracket]
  freeTextTokens.value = [...tokens.freeText]
  filterName.value = tokens.bracket[0] ?? ''
}

function handleSelect(item: Record<string, unknown>): void {
  selectEntry(entryOf(item))
}

function removeBracketToken(index: number): void {
  bracketTokens.value = bracketTokens.value.filter((_, i) => i !== index)
}

function removeFreeTextToken(index: number): void {
  freeTextTokens.value = freeTextTokens.value.filter((_, i) => i !== index)
}

function clearBracketTokens(): void {
  bracketTokens.value = []
}

function clearFreeTextTokens(): void {
  freeTextTokens.value = []
}

function addManualToken(): void {
  const trimmed = manualInput.value.trim().slice(0, 100)
  if (!trimmed) return
  if (manualTokens.value.includes(trimmed)) return
  manualTokens.value.push(trimmed)
  manualInput.value = ''
}

function removeManualToken(index: number): void {
  manualTokens.value = manualTokens.value.filter((_, i) => i !== index)
}

function clearManualTokens(): void {
  manualTokens.value = []
}

const selectedKeywords = computed(() => [
  ...bracketTokens.value,
  ...freeTextTokens.value,
  ...manualTokens.value,
])

// ---------------------------------------------------------------------------
// Section 3 — naming + match count + save
// ---------------------------------------------------------------------------

const filterName = ref('')
const matchCount = ref<number | null>(null)
const matchOverCap = ref(false)
let matchTimer: ReturnType<typeof setTimeout> | null = null

function scheduleMatchCount(): void {
  if (matchTimer !== null) {
    clearTimeout(matchTimer)
    matchTimer = null
  }
  matchTimer = setTimeout(() => {
    api
      .filterMatchCount(selectedKeywords.value)
      .then((result) => {
        matchCount.value = result.count
        matchOverCap.value = result.over_cap
      })
      .catch(() => {
        matchCount.value = null
        matchOverCap.value = false
      })
  }, 500)
}

watch(selectedKeywords, () => {
  if (!selectedEntry.value) return
  scheduleMatchCount()
})

const matchCountClass = computed(() => {
  if (matchCount.value === null) return 'ag-match-neutral'
  if (matchCount.value === 0) return 'ag-match-danger'
  if (matchCount.value > 200) return 'ag-match-warning'
  return 'ag-match-neutral'
})

const canConfirm = computed(() => selectedEntry.value !== null && filterName.value.trim() !== '')

function handleCancel(): void {
  visible.value = false
}

async function handleConfirm(): Promise<void> {
  if (!canConfirm.value) return
  const filter: BtFilter = {
    id: -Date.now(),
    name: filterName.value,
    keywords: [...bracketTokens.value, ...freeTextTokens.value, ...manualTokens.value],
    enabled: true,
    sort_order: props.nextSortOrder,
    created_at: '',
    updated_at: '',
  }

  if (props.mode === 'save-immediately') {
    try {
      const current = await api.listFilters()
      await api.replaceFilters([...current, filter])
      ElMessage.success('已新增過濾器')
      visible.value = false
    } catch (err) {
      ElMessage.error(`新增過濾器失敗：${(err as Error).message}`)
    }
    return
  }

  emit('filter-created', filter)
  visible.value = false
}

// ---------------------------------------------------------------------------
// Reset on open
// ---------------------------------------------------------------------------

function resetState(): void {
  searchQuery.value = ''
  selectedEntry.value = null
  bracketTokens.value = []
  freeTextTokens.value = []
  manualTokens.value = []
  manualInput.value = ''
  filterName.value = ''
  matchCount.value = null
  matchOverCap.value = false
  if (searchTimer !== null) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  if (matchTimer !== null) {
    clearTimeout(matchTimer)
    matchTimer = null
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    resetState()
    void loadFeeds()
    if (props.initialEntry) {
      selectEntry(props.initialEntry)
    }
  },
)
</script>

<template>
  <el-dialog
    v-model="visible"
    title="從標題匯入"
    :width="isMobile ? '100%' : '640px'"
    :fullscreen="isMobile"
  >
    <div
      v-if="!props.initialEntry"
      class="ag-import-section"
    >
      <div class="ag-section-heading">
        1. 搜尋標題
      </div>
      <el-autocomplete
        v-model="searchQuery"
        :fetch-suggestions="fetchSuggestions"
        placeholder="輸入標題關鍵字搜尋歷史抓取紀錄..."
        clearable
        class="ag-search-input"
        @select="handleSelect"
      >
        <template #default="{ item }">
          <div class="ag-suggestion">
            <div class="ag-suggestion-title">
              {{ entryOf(item).title }}
            </div>
            <div class="ag-suggestion-meta">
              <span>{{ feedName(entryOf(item).feed_id) }}</span>
              <span>{{ formatRelativeBare(entryOf(item).fetched_at) }}</span>
            </div>
          </div>
        </template>
      </el-autocomplete>
    </div>

    <template v-if="selectedEntry">
      <div class="ag-import-section">
        <div class="ag-section-heading">
          {{ props.initialEntry ? '1. 預覽 tokens（可編輯）' : '2. 預覽 tokens（可編輯）' }}
        </div>
        <div class="ag-raw-title">
          {{ selectedEntry.title }}
        </div>

        <div class="ag-token-group">
          <div class="ag-token-group-header">
            <span>括號內 tokens</span>
            <el-button
              size="small"
              link
              @click="clearBracketTokens"
            >
              全部清空
            </el-button>
          </div>
          <div class="ag-token-chips">
            <el-tag
              v-for="(tok, idx) in bracketTokens"
              :key="`bracket-${idx}-${tok}`"
              closable
              @close="removeBracketToken(idx)"
            >
              {{ tok }}
            </el-tag>
            <span
              v-if="bracketTokens.length === 0"
              class="ag-muted"
            >（無）</span>
          </div>
        </div>

        <div class="ag-token-group">
          <div class="ag-token-group-header">
            <span>括號外自由文字</span>
            <el-button
              size="small"
              link
              @click="clearFreeTextTokens"
            >
              全部清空
            </el-button>
          </div>
          <div class="ag-token-chips">
            <el-tag
              v-for="(tok, idx) in freeTextTokens"
              :key="`free-${idx}-${tok}`"
              closable
              @close="removeFreeTextToken(idx)"
            >
              {{ tok }}
            </el-tag>
            <span
              v-if="freeTextTokens.length === 0"
              class="ag-muted"
            >（無）</span>
          </div>
        </div>

        <div class="ag-token-group">
          <div class="ag-token-group-header">
            <span>手動加入</span>
            <el-button
              size="small"
              link
              @click="clearManualTokens"
            >
              全部清空
            </el-button>
          </div>
          <div class="ag-manual-input-row">
            <el-input
              v-model="manualInput"
              placeholder="輸入自訂關鍵字（按 Enter 或點新增）"
              class="ag-manual-input"
              @keyup.enter="addManualToken"
            />
            <el-button
              size="small"
              @click="addManualToken"
            >
              新增
            </el-button>
          </div>
          <div class="ag-token-chips">
            <el-tag
              v-for="(tok, idx) in manualTokens"
              :key="`manual-${idx}-${tok}`"
              closable
              @close="removeManualToken(idx)"
            >
              {{ tok }}
            </el-tag>
            <span
              v-if="manualTokens.length === 0"
              class="ag-muted"
            >（無）</span>
          </div>
        </div>
      </div>

      <div class="ag-import-section">
        <div class="ag-section-heading">
          {{ props.initialEntry ? '2. 命名 + 儲存' : '3. 命名 + 儲存' }}
        </div>
        <el-input
          v-model="filterName"
          placeholder="過濾器名稱"
          class="ag-name-input"
        />
        <div
          class="ag-match-count"
          :class="matchCountClass"
        >
          此組合會命中資料庫 {{ matchCount === null ? '…' : matchCount }} 筆歷史紀錄<template v-if="matchOverCap">
            （僅計算最近 10000 筆）
          </template>
        </div>
      </div>
    </template>

    <template #footer>
      <el-button @click="handleCancel">
        取消
      </el-button>
      <el-button
        type="primary"
        :disabled="!canConfirm"
        @click="handleConfirm"
      >
        確定匯入
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.ag-import-section {
  padding: 12px 0;
  border-bottom: 1px solid var(--el-border-color, #e4e7ed);
}
.ag-import-section:last-of-type {
  border-bottom: none;
}
.ag-section-heading {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  margin-bottom: 8px;
}
.ag-search-input {
  width: 100%;
}
.ag-suggestion-title {
  font-weight: 700;
}
.ag-suggestion-meta {
  display: flex;
  gap: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.ag-raw-title {
  word-break: break-all;
  white-space: normal;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin-bottom: 12px;
}
.ag-token-group {
  margin-bottom: 12px;
}
.ag-token-group-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}
.ag-token-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.ag-muted {
  color: #9ca3af;
}
.ag-manual-input-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
.ag-manual-input {
  flex: 1;
}
.ag-name-input {
  margin-bottom: 8px;
}
.ag-match-count {
  font-size: 12px;
}
.ag-match-neutral {
  color: #9ca3af;
}
.ag-match-danger {
  color: var(--el-color-danger);
}
.ag-match-warning {
  color: var(--el-color-warning);
}
</style>
