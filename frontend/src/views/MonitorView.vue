<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { CirclePlus } from '@element-plus/icons-vue'
import { useProgressStore, TERMINAL_STATUSES } from '@/stores/progress'
import { categorize } from '@/composables/useTaskCategory'
import type { TaskProgressEntry } from '@/types'
import MonitorHeader from '@/components/monitor/MonitorHeader.vue'
import MonitorColumn from '@/components/monitor/MonitorColumn.vue'
import ManualTaskDialog from '@/components/ManualTaskDialog.vue'

const manualOpen = ref(false)

const store = useProgressStore()

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

      <div
        v-else
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

@media (max-width: 767px) {
  .monitor-skeleton-grid,
  .monitor-grid {
    grid-template-columns: 1fr;
    /* On mobile the columns stack; let the browser page scroll naturally */
    height: auto;
  }
}
</style>
