<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { CirclePlus } from '@element-plus/icons-vue'
import { useProgressStore, TERMINAL_STATUSES } from '@/stores/progress'
import { categorize } from '@/composables/useTaskCategory'
import { useBreakpoint } from '@/composables/useBreakpoint'
import type { MonitorViewMode, TaskProgressEntry } from '@/types'
import MonitorHeader from '@/components/monitor/MonitorHeader.vue'
import MonitorColumn from '@/components/monitor/MonitorColumn.vue'
import MonitorTable from '@/components/monitor/MonitorTable.vue'
import ManualTaskDialog from '@/components/ManualTaskDialog.vue'

const manualOpen = ref(false)

const store = useProgressStore()
const { isMobile } = useBreakpoint()

// ---------------------------------------------------------------------------
// View mode (table / kanban) — persisted to localStorage, following the
// same read-on-init / guarded-write pattern as useDarkMode.ts.
// ---------------------------------------------------------------------------
const VIEW_MODE_STORAGE_KEY = 'monitor-view-mode'

function readStoredViewMode(): MonitorViewMode {
  if (typeof localStorage === 'undefined') return 'kanban'
  const raw = localStorage.getItem(VIEW_MODE_STORAGE_KEY)
  return raw === 'table' || raw === 'kanban' ? raw : 'kanban'
}

const viewMode = ref<MonitorViewMode>(readStoredViewMode())

function setViewMode(mode: MonitorViewMode): void {
  viewMode.value = mode
  try {
    localStorage.setItem(VIEW_MODE_STORAGE_KEY, mode)
  } catch {
    /* storage may be unavailable; ignore */
  }
}

/**
 * Table mode is unusable at phone width, so mobile always renders kanban
 * (stacked single-column). The underlying `viewMode` preference is left
 * untouched while forced — the toggle is hidden on mobile so nothing can
 * write to it anyway — so the user's real preference reappears exactly as
 * they left it the moment the viewport crosses back above the mobile
 * breakpoint.
 */
const effectiveViewMode = computed<MonitorViewMode>(() =>
  isMobile.value ? 'kanban' : viewMode.value,
)

/** True only during the initial connect before any message has arrived. */
const isInitialLoad = computed(
  () => store.state.value === 'connecting' && !store.hasReceivedFirst.value,
)

/** Whether the overlay should be dimmed (disconnected with last snapshot). */
const isDisconnected = computed(() => store.state.value === 'closed')

/** Last non-empty entries for the dimmed overlay when disconnected. */
const lastEntries = computed((): TaskProgressEntry[] =>
  Object.entries(store.lastTasks.value)
    .filter(([, entry]) => !TERMINAL_STATUSES.has(entry.status))
    .sort(([a], [b]) => Number(b) - Number(a))
    .map(([, entry]) => entry),
)

/** Whether the columns should appear dimmed (disconnected with stale data). */
const columnsDimmed = computed(
  () => isDisconnected.value && lastEntries.value.length > 0,
)

interface ByCategory {
  downloading: TaskProgressEntry[]
  waiting: TaskProgressEntry[]
  completed: TaskProgressEntry[]
}

function partitionEntries(entries: TaskProgressEntry[]): ByCategory {
  const result: ByCategory = { downloading: [], waiting: [], completed: [] }
  for (const entry of entries) {
    const cat = categorize(entry.status)
    if (cat === 'waiting') result.waiting.push(entry)
    else if (cat === 'completed') result.completed.push(entry)
    else result.downloading.push(entry)
  }
  return result
}

/**
 * Per-column task lists — live when connected, last snapshot when
 * disconnected with stale data.
 */
const displayByCategory = computed((): ByCategory => {
  if (isDisconnected.value && lastEntries.value.length > 0) {
    return partitionEntries(lastEntries.value)
  }
  return store.byCategory.value
})

const counts = computed(() => ({
  downloading: displayByCategory.value.downloading.length,
  waiting: displayByCategory.value.waiting.length,
  completed: displayByCategory.value.completed.length,
}))

/**
 * All tasks across the three categories, flattened for table mode — reuses
 * the exact same live-vs-last-snapshot source as the kanban columns so the
 * two views never disagree on the underlying data set or dimmed/disconnect
 * state.
 */
const allTasks = computed((): TaskProgressEntry[] => [
  ...displayByCategory.value.waiting,
  ...displayByCategory.value.downloading,
  ...displayByCategory.value.completed,
])

const hasAnyTask = computed(
  () =>
    counts.value.downloading + counts.value.waiting + counts.value.completed > 0,
)

onMounted(() => {
  store.connect()
})
// No onBeforeUnmount close — the store is app-scope; HeaderTaskIndicator also
// uses the same store instance.  The WS lifecycle === app lifecycle.
</script>

<template>
  <div class="monitor-shell">
    <monitor-header
      :counts="counts"
      :connection-state="store.state.value"
      :show-disconnected-banner="store.showDisconnectedBanner.value"
      :view-mode="viewMode"
      :is-mobile="isMobile"
      @update:view-mode="setViewMode"
    />

    <!-- Disconnect banner (3-second grace period before showing) -->
    <el-alert
      v-if="store.showDisconnectedBanner.value"
      title="連線中斷，嘗試重新連線中…"
      type="warning"
      :closable="false"
      class="ag-disconnect-banner"
    />

    <!-- Initial connecting skeleton -->
    <div
      v-if="isInitialLoad"
      class="monitor-skeleton-grid"
    >
      <el-skeleton
        v-for="n in 3"
        :key="n"
        :rows="3"
        animated
        class="ag-skeleton-card"
      />
    </div>

    <!-- Three-column layout -->
    <template v-else>
      <el-empty
        v-if="!hasAnyTask"
        description="目前沒有任務"
      />

      <template v-else>
        <div
          v-if="effectiveViewMode === 'kanban'"
          class="monitor-grid"
        >
          <monitor-column
            title="等待中"
            variant="info"
            :tasks="displayByCategory.waiting"
            :dimmed="columnsDimmed"
          />
          <monitor-column
            title="下載中"
            variant="success"
            :tasks="displayByCategory.downloading"
            :dimmed="columnsDimmed"
          />
          <monitor-column
            title="近期完成"
            variant="primary"
            :tasks="displayByCategory.completed"
            :dimmed="columnsDimmed"
          />
        </div>

        <monitor-table
          v-else
          :tasks="allTasks"
          :dimmed="columnsDimmed"
        />
      </template>
    </template>

    <el-button
      type="primary"
      circle
      class="manual-task-fab"
      title="新增手動任務"
      @click="manualOpen = true"
    >
      <el-icon :size="24">
        <CirclePlus />
      </el-icon>
    </el-button>

    <ManualTaskDialog v-model="manualOpen" />
  </div>
</template>

<style scoped>
.monitor-shell {
  padding: 16px;
  position: relative;
}

.manual-task-fab {
  position: fixed;
  right: 32px;
  bottom: 32px;
  width: 56px;
  height: 56px;
  font-size: 28px;
  font-weight: 600;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
  z-index: 100;
}

.ag-disconnect-banner {
  margin-bottom: 12px;
}

.ag-skeleton-card {
  margin-bottom: 12px;
}

.monitor-skeleton-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}

.monitor-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  /* Each column stretches to fill the explicit row height */
  align-items: stretch;
  gap: 16px;
  /* Reserve height for fixed nav (~72 px) + shell padding (32 px) + header (~72 px) + gap */
  height: calc(100vh - 196px);
}

/* Tablet — kanban keeps 2 columns instead of 3; the 3rd (近期完成) simply
   wraps onto its own row below rather than cramming into a third,
   too-narrow column. Height goes auto so the page (not each column)
   scrolls, same rationale as the mobile rule below. */
@media (min-width: 768px) and (max-width: 1023px) {
  .monitor-skeleton-grid,
  .monitor-grid {
    grid-template-columns: repeat(2, 1fr);
    height: auto;
  }
}

@media (max-width: 767px) {
  .monitor-skeleton-grid,
  .monitor-grid {
    grid-template-columns: 1fr;
    /* On mobile the columns stack; let the browser page scroll naturally */
    height: auto;
  }
}
</style>
