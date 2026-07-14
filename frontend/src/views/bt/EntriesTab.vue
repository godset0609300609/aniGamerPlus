<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { BtApi } from '@/api/bt'
import {
  BT_STATUS_FILTER_OPTIONS,
  resolveLabel,
  resolveTagClass,
  resolveTagType,
  resolveTooltip,
} from '@/utils/btStatus'
import { formatRelativeBare } from '@/utils/format'
import { useAutoRefresh } from '@/composables/useAutoRefresh'
import { useBreakpoint } from '@/composables/useBreakpoint'
import FiltersImportDialog from './FiltersImportDialog.vue'
import type { BtFeedEntry, BtFilter } from '@/types'

const { isMobile } = useBreakpoint()

const NULL_STATUS_FILTER = '__unassigned__'
const ALL_FILTERS = ''
const PAGE_SIZES = [10, 20, 50, 100]
const SEARCH_DEBOUNCE_MS = 300

const api = new BtApi()
const route = useRoute()

const entries = ref<BtFeedEntry[]>([])
const feedNames = ref<Record<number, string>>({})
const filters = ref<BtFilter[]>([])
const filterNames = ref<Record<number, string>>({})
const loading = ref(false)
const statusFilter = ref<string>('')
const filterIdFilter = ref<string>(ALL_FILTERS)
const searchQuery = ref('')
const page = ref(1)
const size = ref(50)
const total = ref(0)

function feedName(feedId: number): string {
  return feedNames.value[feedId] ?? `#${feedId}`
}

function filterName(filterId: number | null): string {
  if (filterId === null) return '—'
  return filterNames.value[filterId] ?? `#${filterId}`
}

function currentFilterId(): number | undefined {
  if (!filterIdFilter.value) return undefined
  const n = Number(filterIdFilter.value)
  return Number.isFinite(n) && n > 0 ? n : undefined
}

async function fetchEntries(): Promise<void> {
  const fid = currentFilterId()
  const q = searchQuery.value.trim() || undefined
  const status = statusFilter.value || undefined
  const result = await api.listEntries(7, fid, page.value, size.value, q, status)
  entries.value = result.items
  total.value = result.total
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const [feeds, filtersList] = await Promise.all([api.listFeeds(), api.listFilters()])
    feedNames.value = Object.fromEntries(feeds.map((f) => [f.id, f.name]))
    filters.value = filtersList
    filterNames.value = Object.fromEntries(filtersList.map((f) => [f.id, f.name]))

    const queryFilter = route.query.filter
    if (typeof queryFilter === 'string' && queryFilter) {
      const desired = Number(queryFilter)
      if (Number.isFinite(desired) && filtersList.some((f) => f.id === desired)) {
        filterIdFilter.value = String(desired)
      }
    }

    await fetchEntries()
  } catch (err) {
    ElMessage.error(`讀取抓取紀錄失敗：${(err as Error).message}`)
  } finally {
    loading.value = false
  }
}

const initialLoadDone = ref(false)

async function refetch(): Promise<void> {
  if (!initialLoadDone.value) return
  loading.value = true
  try {
    await fetchEntries()
  } catch (err) {
    ElMessage.error(`讀取抓取紀錄失敗：${(err as Error).message}`)
  } finally {
    loading.value = false
  }
}

let searchTimer: ReturnType<typeof setTimeout> | null = null

watch(searchQuery, () => {
  if (searchTimer !== null) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  searchTimer = setTimeout(() => {
    page.value = 1
    void refetch()
  }, SEARCH_DEBOUNCE_MS)
})

watch(filterIdFilter, () => {
  if (!initialLoadDone.value) return
  page.value = 1
  void refetch()
})

watch(statusFilter, () => {
  if (!initialLoadDone.value) return
  page.value = 1
  void refetch()
})

function handleSizeChange(newSize: number): void {
  size.value = newSize
  page.value = 1
  void refetch()
}

function handleCurrentChange(newPage: number): void {
  page.value = newPage
  void refetch()
}

// ---------------------------------------------------------------------------
// "匯入過濾器" — jump straight into FiltersImportDialog with this row pre-selected.
// ---------------------------------------------------------------------------

const importDialogVisible = ref(false)
const importEntry = ref<BtFeedEntry | null>(null)

function openImportDialog(row: BtFeedEntry): void {
  importEntry.value = row
  importDialogVisible.value = true
}

async function refreshFilters(): Promise<void> {
  try {
    const filtersList = await api.listFilters()
    filters.value = filtersList
    filterNames.value = Object.fromEntries(filtersList.map((f) => [f.id, f.name]))
  } catch {
    // Best-effort — the dropdown/labels stay stale until the next manual refresh.
  }
}

watch(importDialogVisible, (visible) => {
  if (visible) return
  void refreshFilters()
})

const nextSortOrder = computed(() => filters.value.reduce((m, f) => Math.max(m, f.sort_order), -1) + 1)

// ---------------------------------------------------------------------------
// "派送 Put.io" / "重新派送" — manual dispatch, independent of match state.
// ---------------------------------------------------------------------------

const dispatchingIds = ref<Set<number>>(new Set())

function isDispatching(entryId: number): boolean {
  return dispatchingIds.value.has(entryId)
}

async function performDispatch(row: BtFeedEntry): Promise<void> {
  dispatchingIds.value.add(row.id)
  try {
    const result = await api.dispatchEntry(row.id)
    if (result.status === 'ALREADY_ADDED') {
      // Benign: the link was already an active transfer on Put.io — not a
      // failure, so this reads as informational rather than a red error.
      ElMessage.info('此項目已在 Put.io，無需重複派送')
    } else {
      ElMessage.success('已派送至 Put.io')
    }
    await fetchEntries()
  } catch (err) {
    ElMessage.error(`派送失敗：${(err as Error).message}`)
  } finally {
    dispatchingIds.value.delete(row.id)
  }
}

async function handleDispatchClick(row: BtFeedEntry): Promise<void> {
  if (row.putio_transfer_id) {
    try {
      await ElMessageBox.confirm('已派送過，確定要再次派送？', '確認', {
        confirmButtonText: '確定',
        cancelButtonText: '取消',
        type: 'warning',
      })
    } catch {
      return
    }
  }
  await performDispatch(row)
}

onMounted(async () => {
  await load()
  initialLoadDone.value = true
})

// Fix 5 — this tab is where a user parks to watch a BT dispatch/landing
// pipeline progress; poll every 5s (visibility-gated) so putio_status /
// local_path updates show up without a manual refresh. Uses the lighter
// `refetch` (entries only) rather than `load` (feeds + filters + entries)
// since the latter two rarely change during a polling window.
useAutoRefresh(5000, refetch)
</script>

<template>
  <div class="ag-bt-entries">
    <div class="ag-toolbar">
      <el-input
        v-model="searchQuery"
        placeholder="搜尋標題"
        clearable
        class="ag-search-input"
      >
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>
      <el-select
        v-model="statusFilter"
        placeholder="全部狀態"
        clearable
        class="ag-status-filter"
      >
        <el-option
          v-for="s in BT_STATUS_FILTER_OPTIONS"
          :key="s"
          :label="resolveLabel(s)"
          :value="s"
        />
        <el-option
          label="未派送"
          :value="NULL_STATUS_FILTER"
        />
      </el-select>
      <el-select
        v-model="filterIdFilter"
        placeholder="全部過濾器"
        clearable
        class="ag-filter-id-filter"
      >
        <el-option
          v-for="f in filters"
          :key="f.id"
          :label="f.name"
          :value="String(f.id)"
        />
      </el-select>
      <el-button
        :loading="loading"
        @click="load"
      >
        重新整理
      </el-button>
    </div>

    <el-table
      v-if="!isMobile"
      :data="entries"
      stripe
      size="small"
      class="ag-entries-table"
      empty-text=" "
    >
      <el-table-column
        label="標題"
        min-width="260"
      >
        <template #default="{ row }">
          <el-tooltip
            :content="row.title"
            placement="top"
          >
            <span>{{ row.title }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column
        label="來源"
        width="140"
      >
        <template #default="{ row }">
          {{ feedName(row.feed_id) }}
        </template>
      </el-table-column>
      <el-table-column
        label="命中過濾器"
        width="140"
      >
        <template #default="{ row }">
          {{ filterName(row.matched_filter_id) }}
        </template>
      </el-table-column>
      <el-table-column
        label="Put.io 狀態"
        width="140"
      >
        <template #default="{ row }">
          <el-tooltip
            v-if="row.putio_status && resolveTooltip(row.putio_status)"
            :content="resolveTooltip(row.putio_status)!"
            placement="top"
          >
            <el-tag
              :type="resolveTagType(row.putio_status)"
              :class="resolveTagClass(row.putio_status)"
              size="small"
            >
              {{ resolveLabel(row.putio_status) }}
            </el-tag>
          </el-tooltip>
          <el-tag
            v-else-if="row.putio_status"
            :type="resolveTagType(row.putio_status)"
            :class="resolveTagClass(row.putio_status)"
            size="small"
          >
            {{ resolveLabel(row.putio_status) }}
          </el-tag>
          <span
            v-else
            class="ag-muted"
          >未派送</span>
        </template>
      </el-table-column>
      <el-table-column
        label="落地路徑"
        min-width="200"
      >
        <template #default="{ row }">
          <span v-if="row.local_path">{{ row.local_path }}</span>
          <span
            v-else
            class="ag-muted"
          >—</span>
        </template>
      </el-table-column>
      <el-table-column
        label="收錄時間"
        width="140"
        align="right"
      >
        <template #default="{ row }">
          <el-tooltip
            :content="row.fetched_at"
            placement="top"
          >
            <span>{{ formatRelativeBare(row.fetched_at) }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column
        label="操作"
        width="260"
      >
        <template #default="{ row }">
          <div class="ag-actions">
            <el-button
              v-if="!row.matched_filter_id"
              size="small"
              @click="openImportDialog(row)"
            >
              匯入過濾器
            </el-button>
            <el-button
              size="small"
              :type="row.putio_transfer_id ? 'default' : 'primary'"
              :loading="isDispatching(row.id)"
              @click="handleDispatchClick(row)"
            >
              {{ row.putio_transfer_id ? '重新派送' : '派送 Put.io' }}
            </el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <!-- Mobile: hide the table, render each entry as a stacked card
         (bangumi title -> source badge -> status -> action buttons). -->
    <div
      v-else
      class="ag-entries-cards"
    >
      <div
        v-for="row in entries"
        :key="row.id"
        class="ag-entry-card"
      >
        <div class="ag-entry-card__title">
          {{ row.title }}
        </div>
        <div class="ag-entry-card__badges">
          <el-tag
            type="info"
            size="small"
          >
            {{ feedName(row.feed_id) }}
          </el-tag>
          <el-tooltip
            v-if="row.putio_status && resolveTooltip(row.putio_status)"
            :content="resolveTooltip(row.putio_status)!"
            placement="top"
          >
            <el-tag
              :type="resolveTagType(row.putio_status)"
              :class="resolveTagClass(row.putio_status)"
              size="small"
            >
              {{ resolveLabel(row.putio_status) }}
            </el-tag>
          </el-tooltip>
          <el-tag
            v-else-if="row.putio_status"
            :type="resolveTagType(row.putio_status)"
            :class="resolveTagClass(row.putio_status)"
            size="small"
          >
            {{ resolveLabel(row.putio_status) }}
          </el-tag>
          <span
            v-else
            class="ag-muted"
          >未派送</span>
        </div>
        <div class="ag-entry-card__meta">
          <span>命中：{{ filterName(row.matched_filter_id) }}</span>
          <el-tooltip
            :content="row.fetched_at"
            placement="top"
          >
            <span>{{ formatRelativeBare(row.fetched_at) }}</span>
          </el-tooltip>
        </div>
        <div
          v-if="row.local_path"
          class="ag-entry-card__path"
        >
          {{ row.local_path }}
        </div>
        <div class="ag-entry-card__actions">
          <el-button
            v-if="!row.matched_filter_id"
            size="small"
            @click="openImportDialog(row)"
          >
            匯入過濾器
          </el-button>
          <el-button
            size="small"
            :type="row.putio_transfer_id ? 'default' : 'primary'"
            :loading="isDispatching(row.id)"
            @click="handleDispatchClick(row)"
          >
            {{ row.putio_transfer_id ? '重新派送' : '派送 Put.io' }}
          </el-button>
        </div>
      </div>
    </div>

    <div
      v-if="!loading && entries.length === 0"
      class="ag-empty"
    >
      沒有符合條件的抓取紀錄。
    </div>

    <el-pagination
      :total="total"
      :current-page="page"
      :page-size="size"
      :page-sizes="PAGE_SIZES"
      layout="total, sizes, prev, pager, next"
      class="ag-pagination"
      @size-change="handleSizeChange"
      @current-change="handleCurrentChange"
    />

    <FiltersImportDialog
      v-model="importDialogVisible"
      :next-sort-order="nextSortOrder"
      :initial-entry="importEntry"
      mode="save-immediately"
    />
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
.ag-search-input {
  width: 240px;
}
.ag-status-filter {
  width: 180px;
}
.ag-filter-id-filter {
  width: 200px;
}
.ag-entries-table {
  width: 100%;
}
/* Uniform row height — titles/paths vary wildly in length, which otherwise
   makes the table look ragged (some rows wrap to 2 lines, others 1). */
.ag-entries-table :deep(.el-table__row) {
  height: 56px;
}
.ag-entries-table :deep(.el-table__row td) {
  vertical-align: middle;
}
.ag-entries-table :deep(.el-table__row td .cell) {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ag-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}
.ag-muted {
  color: #9ca3af;
}
/* '遠端已清理' — no built-in Element Plus tag type reads as teal, so this
   overrides the plain (type="") tag's color variables directly. */
.ag-tag-remote-cleared {
  --el-tag-bg-color: rgba(20, 184, 166, 0.1);
  --el-tag-text-color: #0d9488;
  --el-tag-border-color: rgba(20, 184, 166, 0.3);
}
.ag-empty {
  text-align: center;
  color: #9ca3af;
  padding: 32px 0;
}
.ag-pagination {
  margin-top: 16px;
  justify-content: flex-end;
}

/* ---------------------------------------------------------------------
   Mobile card mode.
   --------------------------------------------------------------------- */
.ag-entries-cards {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.ag-entry-card {
  border: 1px solid var(--el-border-color, #e4e7ed);
  border-radius: 8px;
  padding: 12px;
  background: var(--el-bg-color, #fff);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ag-entry-card__title {
  font-weight: 600;
  word-break: break-word;
}
.ag-entry-card__badges {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.ag-entry-card__meta {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.ag-entry-card__path {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  word-break: break-all;
}
.ag-entry-card__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding-top: 4px;
  border-top: 1px solid var(--el-border-color-lighter, #ebeef5);
}

@media (max-width: 767px) {
  .ag-search-input,
  .ag-status-filter,
  .ag-filter-id-filter {
    width: 100%;
  }
}
</style>
