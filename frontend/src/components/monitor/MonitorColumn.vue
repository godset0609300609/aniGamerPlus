<script setup lang="ts">
import type { TaskProgressEntry } from '@/types'
import type { TaskCategory } from '@/composables/useTaskCategory'
import TaskCard from './TaskCard.vue'

defineProps<{
  title: string
  variant: 'success' | 'info' | 'danger' | 'primary'
  tasks: TaskProgressEntry[]
  dimmed?: boolean
}>()

function categoryVariant(v: 'success' | 'info' | 'danger' | 'primary'): TaskCategory {
  if (v === 'success') return 'downloading'
  if (v === 'primary') return 'completed'
  return 'waiting'
}
</script>

<template>
  <div
    class="monitor-column"
    :class="{ 'monitor-column--dimmed': dimmed }"
  >
    <div
      class="monitor-column__header"
      :class="`monitor-column__header--${variant}`"
    >
      <span class="monitor-column__title">{{ title }}</span>
      <el-tag
        :type="variant === 'primary' ? 'primary' : variant"
        size="small"
        class="monitor-column__count"
      >
        {{ tasks.length }}
      </el-tag>
    </div>

    <el-scrollbar class="monitor-column__body">
      <el-empty
        v-if="tasks.length === 0"
        :description="`${title} 沒有任務`"
      />
      <task-card
        v-for="task in tasks"
        :key="`${task.sn}-${task.started_at ?? ''}`"
        :task="task"
        :variant="categoryVariant(variant)"
      />
    </el-scrollbar>
  </div>
</template>

<style scoped>
.monitor-column {
  background: var(--el-bg-color, #f5f5f5);
  border-radius: 8px;
  padding: 0;
  display: flex;
  flex-direction: column;
  /* fill the grid row height; parent (.monitor-grid) sets align-items: stretch */
  overflow: hidden;
  transition: opacity 0.2s, filter 0.2s;
}

.monitor-column--dimmed {
  opacity: 0.5;
  filter: grayscale(1);
}

/* Fixed header — never scrolls away */
.monitor-column__header {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 2px solid var(--el-border-color-lighter);
  border-radius: 8px 8px 0 0;
}

.monitor-column__header--success {
  border-bottom-color: var(--el-color-success);
}

.monitor-column__header--info {
  border-bottom-color: var(--el-color-info);
}

.monitor-column__header--danger {
  border-bottom-color: var(--el-color-danger);
}

.monitor-column__header--primary {
  border-bottom-color: var(--el-color-primary);
}

.monitor-column__title {
  font-weight: 700;
  font-size: 0.95em;
}

.monitor-column__count {
  flex-shrink: 0;
}

/* Scrollable body — el-scrollbar fills remaining space */
.monitor-column__body {
  flex: 1;
  min-height: 0; /* required so flex child can shrink below content size */
}

/* el-scrollbar renders a wrapper div; give it full height too */
.monitor-column__body :deep(.el-scrollbar__wrap) {
  height: 100%;
}
</style>
