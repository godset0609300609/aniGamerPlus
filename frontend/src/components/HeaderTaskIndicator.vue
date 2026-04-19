<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { Download, Warning } from '@element-plus/icons-vue'
import { useProgressStore } from '@/stores/progress'
import MiniTaskCard from './MiniTaskCard.vue'

const router = useRouter()
const store = useProgressStore()

const iconClass = computed(() => {
  if (store.retryCount.value > 0) return 'ag-indicator-error'
  if (store.downloadingCount.value > 0) return 'ag-indicator-downloading'
  return 'ag-indicator-waiting'
})

const topTasks = computed(() => store.activeEntries.value.slice(0, 5))
const remaining = computed(() => Math.max(0, store.totalCount.value - 5))

function goToMonitor() {
  void router.push('/monitor')
}
</script>

<template>
  <el-popover
    v-if="store.totalCount.value > 0"
    placement="bottom-end"
    trigger="hover"
    :width="320"
  >
    <template #reference>
      <el-badge
        :value="store.totalCount.value"
        :max="99"
      >
        <el-button
          circle
          title="任務進行中"
          :class="iconClass"
          @click="goToMonitor"
        >
          <el-icon :size="18">
            <Warning v-if="store.retryCount.value > 0" />
            <Download v-else />
          </el-icon>
        </el-button>
      </el-badge>
    </template>
    <div class="indicator-popover">
      <MiniTaskCard
        v-for="entry in topTasks"
        :key="entry.filename"
        :task="entry"
      />
      <div
        v-if="remaining > 0"
        class="indicator-more"
      >
        還有 {{ remaining }} 個任務…
      </div>
    </div>
  </el-popover>
</template>

<style scoped>
/* Bounce download indicator — Download icon has a clear top/bottom direction,
   so bouncing downward ("things keep arriving") is more intuitive than spinning. */
.ag-indicator-downloading :deep(.el-icon) {
  animation: ag-bounce 1.2s ease-in-out infinite;
}

@keyframes ag-bounce {
  0%, 100% { transform: translateY(0); }
  50%       { transform: translateY(3px); }
}

.ag-indicator-error {
  color: var(--el-color-danger) !important;
  border-color: var(--el-color-danger) !important;
}

.indicator-popover {
  max-height: 320px;
  overflow-y: auto;
}

.indicator-more {
  text-align: center;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  padding-top: 8px;
}
</style>
