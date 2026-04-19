<script setup lang="ts">
import type { SocketState } from '@/api/ws'

defineProps<{
  counts: { downloading: number; waiting: number; completed: number }
  connectionState: SocketState
  showDisconnectedBanner: boolean
}>()
</script>

<template>
  <div class="monitor-header">
    <div class="monitor-header__left">
      <h2 class="monitor-header__title">
        任務監控
      </h2>
      <span
        class="monitor-header__dot"
        :class="{
          'monitor-header__dot--open': connectionState === 'open',
          'monitor-header__dot--connecting': connectionState === 'connecting',
          'monitor-header__dot--closed': connectionState === 'closed' && showDisconnectedBanner,
        }"
        :title="connectionState"
      />
    </div>
    <div class="monitor-header__badges">
      <el-tag
        type="info"
        size="default"
      >
        等待中 {{ counts.waiting }}
      </el-tag>
      <el-tag
        type="success"
        size="default"
      >
        下載中 {{ counts.downloading }}
      </el-tag>
      <el-tag
        type="primary"
        size="default"
      >
        近期完成 {{ counts.completed }}
      </el-tag>
    </div>
  </div>
</template>

<style scoped>
.monitor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}

.monitor-header__left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.monitor-header__title {
  margin: 0;
  font-size: 1.3em;
}

.monitor-header__dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--el-color-info);
  transition: background 0.3s;
}

.monitor-header__dot--open {
  background: var(--el-color-success);
}

.monitor-header__dot--connecting {
  background: var(--el-color-warning);
}

.monitor-header__dot--closed {
  background: var(--el-color-danger);
}

.monitor-header__badges {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
