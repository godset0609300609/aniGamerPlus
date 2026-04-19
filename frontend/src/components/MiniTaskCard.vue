<script setup lang="ts">
import { computed } from 'vue'
import { ElProgress } from 'element-plus'
import type { TaskProgressEntry } from '@/types'

const props = defineProps<{
  task: TaskProgressEntry
}>()

const displayName = computed(() => {
  if (props.task.bangumi_name) {
    const ep = props.task.episode ? ` EP ${props.task.episode}` : ''
    return `《${props.task.bangumi_name}》${ep}`
  }
  return props.task.filename
})

const pct = computed(() => Math.round(props.task.rate))
</script>

<template>
  <div class="mini-task-card">
    <div class="mini-task-card__top">
      <span class="mini-task-card__name">{{ displayName }}</span>
      <span class="mini-task-card__pct">{{ pct }}%</span>
      <span class="mini-task-card__status">{{ task.status }}</span>
    </div>
    <ElProgress
      :percentage="pct"
      :stroke-width="4"
      :show-text="false"
      class="mini-task-card__bar"
    />
  </div>
</template>

<style scoped>
.mini-task-card {
  padding: 6px 0;
  border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
  font-size: 12px;
}

.mini-task-card:last-child {
  border-bottom: none;
}

.mini-task-card__top {
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex-wrap: wrap;
}

.mini-task-card__name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--el-text-color-primary, #303133);
}

.mini-task-card__pct {
  font-weight: 600;
  color: var(--el-color-primary, #409eff);
  white-space: nowrap;
}

.mini-task-card__status {
  color: var(--el-text-color-secondary, #909399);
  white-space: nowrap;
}

.mini-task-card__bar {
  margin-top: 2px;
}
</style>
