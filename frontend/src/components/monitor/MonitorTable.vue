<script setup lang="ts">
import { computed, ref } from 'vue'
import type { TaskProgressEntry } from '@/types'
import { categorize } from '@/composables/useTaskCategory'
import { clampPercentage, ownerInitials, taskDisplayTitle } from '@/utils/format'
import { sourceBadgeColor, sourceBadgeLabel, sourceBadgeTextColor } from '@/utils/sourceBadge'
import { dismissTask } from '@/utils/taskActions'
import {
  buildFilterOptions,
  compareNullableAsc,
  compareNullableDesc,
  filterByOwner,
  filterBySource,
  filterByStatus,
  formatEtaClock,
  formatSpeed,
} from '@/utils/monitorTable'

const props = defineProps<{
  tasks: TaskProgressEntry[]
  dimmed?: boolean
}>()

// ---------------------------------------------------------------------------
// Sorting — fully self-managed (rather than el-table's built-in default
// comparator) so speed_mbps/eta_seconds can guarantee null values always
// sink to the bottom regardless of ascending/descending direction. Columns
// use `sortable="custom"` + `@sort-change`, which only tells us *what* the
// user wants sorted by; the actual comparison lives in `sortedRows` below.
// ---------------------------------------------------------------------------
type SortProp = 'title' | 'status' | 'speed_mbps' | 'eta_seconds' | 'started_at'

interface SortState {
  prop: SortProp
  order: 'ascending' | 'descending'
}

// Newest task first — glancing at the monitor while a batch of downloads is
// running should surface what *just started*, not whatever happens to be
// fastest right now.
const DEFAULT_SORT: SortState = { prop: 'started_at', order: 'descending' }

/** `started_at` as an epoch-ms number for date comparisons, or `null` when absent/unparseable. */
function startedAtMs(row: TaskProgressEntry): number | null {
  if (!row.started_at) return null
  const t = new Date(row.started_at).getTime()
  return Number.isNaN(t) ? null : t
}

const sortState = ref<SortState>({ ...DEFAULT_SORT })

/**
 * `payload` is typed loosely (rather than importing Element Plus's exact
 * `sort-change` event type) so this stays a strict structural supertype of
 * whatever Element Plus actually emits — avoids a brittle coupling to its
 * internal type shape while still being fully typesafe at the call site.
 */
function handleSortChange(payload: { prop?: string; order?: string | null }): void {
  if (payload.order !== 'ascending' && payload.order !== 'descending') {
    sortState.value = { ...DEFAULT_SORT }
    return
  }
  sortState.value = { prop: (payload.prop ?? DEFAULT_SORT.prop) as SortProp, order: payload.order }
}

const sortedRows = computed<TaskProgressEntry[]>(() => {
  const rows = [...props.tasks]
  const { prop, order } = sortState.value
  const dir = order === 'ascending' ? 1 : -1

  switch (prop) {
    case 'title':
      return rows.sort((a, b) => dir * taskDisplayTitle(a).localeCompare(taskDisplayTitle(b)))
    case 'status':
      return rows.sort((a, b) => dir * a.status.localeCompare(b.status))
    case 'eta_seconds':
      return rows.sort((a, b) =>
        order === 'ascending'
          ? compareNullableAsc(a.eta_seconds, b.eta_seconds)
          : compareNullableDesc(a.eta_seconds, b.eta_seconds),
      )
    case 'started_at':
      return rows.sort((a, b) =>
        order === 'ascending'
          ? compareNullableAsc(startedAtMs(a), startedAtMs(b))
          : compareNullableDesc(startedAtMs(a), startedAtMs(b)),
      )
    case 'speed_mbps':
    default:
      return rows.sort((a, b) =>
        order === 'ascending'
          ? compareNullableAsc(a.speed_mbps, b.speed_mbps)
          : compareNullableDesc(a.speed_mbps, b.speed_mbps),
      )
  }
})

// ---------------------------------------------------------------------------
// Filters — real Element Plus `:filters` + `:filter-method` per column,
// dynamic option lists built from the current row set.
// ---------------------------------------------------------------------------
// `row.source` of `null`/`undefined` normalizes to `''` (the sentinel used
// by filterBySource — see monitorTable.ts) rather than `'animad'`, so the
// filter dropdown gets its own "未知" option instead of conflating unknown
// entries with genuine animad ones.
const sourceFilterOptions = computed(() =>
  buildFilterOptions(props.tasks, (row) => row.source ?? '', (value) => sourceBadgeLabel(value || null)),
)
const statusFilterOptions = computed(() => buildFilterOptions(props.tasks, (row) => row.status))
const ownerFilterOptions = computed(() =>
  buildFilterOptions(
    props.tasks.filter((row) => row.owner_username),
    (row) => row.owner_username as string,
  ),
)

function sourceFilterMethod(value: string, row: TaskProgressEntry): boolean {
  return filterBySource(row, value)
}
function statusFilterMethod(value: string, row: TaskProgressEntry): boolean {
  return filterByStatus(row, value)
}
function ownerFilterMethod(value: string, row: TaskProgressEntry): boolean {
  return filterByOwner(row, value)
}

// ---------------------------------------------------------------------------
// Row helpers
// ---------------------------------------------------------------------------
function rowPercentage(row: TaskProgressEntry): number {
  return clampPercentage(row.rate)
}

function ownerInitialsOf(row: TaskProgressEntry): string {
  return row.owner_username ? ownerInitials(row.owner_username) : ''
}

function isCancelable(row: TaskProgressEntry): boolean {
  return categorize(row.status) !== 'completed'
}

async function onCancel(row: TaskProgressEntry): Promise<void> {
  await dismissTask(row.sn)
}
</script>

<template>
  <div
    class="monitor-table"
    :class="{ 'monitor-table--dimmed': dimmed }"
  >
    <el-empty
      v-if="tasks.length === 0"
      description="目前沒有任務"
    />

    <el-table
      v-else
      :data="sortedRows"
      style="width: 100%"
      class="monitor-table__el-table"
      @sort-change="handleSortChange"
    >
      <el-table-column
        label="作品"
        prop="title"
        min-width="200"
        sortable="custom"
      >
        <template #default="{ row }">
          <span class="monitor-table__title">{{ taskDisplayTitle(row) }}</span>
        </template>
      </el-table-column>

      <el-table-column
        label="集"
        width="80"
      >
        <template #default="{ row }">
          {{ row.episode ?? '—' }}
        </template>
      </el-table-column>

      <el-table-column
        label="來源"
        width="100"
        :filters="sourceFilterOptions"
        :filter-method="sourceFilterMethod"
      >
        <template #default="{ row }">
          <el-tag
            size="small"
            :data-color="sourceBadgeColor(row.source)"
            :data-source="row.source ?? 'unknown'"
            :style="{
              backgroundColor: sourceBadgeColor(row.source),
              borderColor: sourceBadgeColor(row.source),
              color: sourceBadgeTextColor(row.source),
            }"
          >
            {{ sourceBadgeLabel(row.source) }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column
        label="狀態"
        prop="status"
        width="120"
        sortable="custom"
        :filters="statusFilterOptions"
        :filter-method="statusFilterMethod"
      >
        <template #default="{ row }">
          {{ row.status }}
        </template>
      </el-table-column>

      <el-table-column
        label="進度"
        min-width="140"
      >
        <template #default="{ row }">
          <el-tooltip
            :content="`${clampPercentage(row.rate)}%`"
            placement="top"
          >
            <el-progress
              :percentage="rowPercentage(row)"
              :stroke-width="6"
              :show-text="false"
            />
          </el-tooltip>
        </template>
      </el-table-column>

      <el-table-column
        label="速度"
        prop="speed_mbps"
        width="90"
        align="right"
        sortable="custom"
      >
        <template #default="{ row }">
          {{ formatSpeed(row.speed_mbps) }}
        </template>
      </el-table-column>

      <el-table-column
        label="ETA"
        prop="eta_seconds"
        width="80"
        align="right"
        sortable="custom"
      >
        <template #default="{ row }">
          {{ formatEtaClock(row.eta_seconds) }}
        </template>
      </el-table-column>

      <el-table-column
        label="擁有者"
        width="120"
        :filters="ownerFilterOptions"
        :filter-method="ownerFilterMethod"
      >
        <template #default="{ row }">
          <span
            v-if="row.owner_username"
            class="monitor-table__owner"
          >
            <el-avatar
              :size="20"
              :src="row.owner_avatar_url"
              class="monitor-table__owner-avatar"
            >{{ ownerInitialsOf(row) }}</el-avatar>
            <span class="monitor-table__owner-name">{{ row.owner_username }}</span>
          </span>
          <span v-else>—</span>
        </template>
      </el-table-column>

      <el-table-column
        label="動作"
        width="100"
      >
        <template #default="{ row }">
          <el-button
            v-if="isCancelable(row)"
            circle
            size="small"
            class="cancel-btn"
            title="取消任務"
            @click.stop="onCancel(row)"
          >
            ✕
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.monitor-table {
  transition: opacity 0.2s, filter 0.2s;
  /* Table mode is only reachable on tablet/desktop (mobile always forces
     kanban — see MonitorView.vue); at tablet width the fixed per-column
     min-widths can still exceed the viewport, so scroll horizontally
     within the table rather than blowing out the page layout. */
  overflow-x: auto;
}

.monitor-table--dimmed {
  opacity: 0.5;
  filter: grayscale(1);
}

.monitor-table__title {
  font-weight: 600;
}

.monitor-table__owner {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.monitor-table__owner-avatar {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  background: var(--el-color-primary);
  color: #fff;
}

.monitor-table__owner-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.monitor-table__el-table :deep(.el-progress-bar__inner) {
  transition: width 0.3s ease-out;
}

@media (prefers-reduced-motion: reduce) {
  .monitor-table__el-table :deep(.el-progress-bar__inner) {
    transition: none !important;
  }
}
</style>
