<script setup lang="ts">
defineProps<{
  retryCount?: number
}>()

const emit = defineEmits<{
  retry: []
}>()

function handleRetry() {
  emit('retry')
}
</script>

<template>
  <Teleport to="body">
    <Transition name="ag-overlay-fade">
      <div
        v-show="true"
        class="ag-offline-overlay"
      >
        <div class="ag-offline-box">
          <div class="ag-offline-spinner" />
          <p class="ag-offline-title">
            後端服務異常，嘗試重新連線…
          </p>
          <p
            v-if="retryCount && retryCount > 0"
            class="ag-offline-attempts"
          >
            嘗試第 {{ retryCount }} 次
          </p>
          <button
            class="ag-offline-retry-btn"
            @click="handleRetry"
          >
            手動重試
          </button>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ag-offline-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgba(0, 0, 0, 0.65);
  display: flex;
  align-items: center;
  justify-content: center;
  backdrop-filter: grayscale(80%);
}

.ag-offline-box {
  background: #1e1e2e;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  padding: 40px 48px;
  text-align: center;
  color: #e0e0e0;
  max-width: 360px;
  width: 90%;
}

.ag-offline-spinner {
  width: 48px;
  height: 48px;
  border: 4px solid rgba(255, 255, 255, 0.15);
  border-top-color: #4caf50;
  border-radius: 50%;
  animation: ag-spin 1s linear infinite;
  margin: 0 auto 20px;
}

@keyframes ag-spin {
  to {
    transform: rotate(360deg);
  }
}

.ag-offline-title {
  font-size: 15px;
  margin: 0 0 8px;
  line-height: 1.5;
}

.ag-offline-attempts {
  font-size: 13px;
  color: #888;
  margin: 0 0 20px;
}

.ag-offline-retry-btn {
  background: #4caf50;
  color: white;
  border: none;
  border-radius: 6px;
  padding: 10px 24px;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s;
}

.ag-offline-retry-btn:hover {
  background: #43a047;
}

/* Fade transition */
.ag-overlay-fade-enter-active,
.ag-overlay-fade-leave-active {
  transition: opacity 0.3s ease;
}

.ag-overlay-fade-enter-from,
.ag-overlay-fade-leave-to {
  opacity: 0;
}
</style>
