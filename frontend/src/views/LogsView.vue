<script setup lang="ts">
import LogStreamPanel from '@/components/LogStreamPanel.vue'
</script>

<template>
  <div class="logs-shell">
    <h1 class="logs-shell__title">
      系統日誌
    </h1>
    <LogStreamPanel
      :always-expanded="true"
      class="logs-shell__panel"
    />
  </div>
</template>

<style scoped>
/*
 * logs-shell occupies the full viewport height minus:
 *   60px — el-header (sticky nav, default Element Plus height)
 *   20px — el-main top padding (Element Plus default)
 *   20px — el-main bottom padding (Element Plus default)
 * = 100px total offset.
 *
 * Mobile: switch to flex-1 / min-height approach so the panel at least
 * fills available space without overflowing small screens.
 */
.logs-shell {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 100px);
  padding: 16px;
  box-sizing: border-box;
}

.logs-shell__title {
  font-size: 20px;
  font-weight: 600;
  margin: 0 0 12px;
  flex-shrink: 0;
}

.logs-shell__panel {
  flex: 1;
  min-height: 0;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  overflow: hidden;
}

/* Mobile: relax the fixed-height constraint; let content expand naturally */
@media (max-width: 768px) {
  .logs-shell {
    height: auto;
    min-height: calc(100vh - 100px);
  }

  .logs-shell__panel {
    min-height: 60vh;
  }
}
</style>
