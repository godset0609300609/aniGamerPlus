<script setup lang="ts">
/**
 * Floating action bar shown while a form has unsaved changes.
 *
 * Extracted from SettingsView.vue so the same pill-shaped "dirty" fab
 * can be reused by AnimeListView (and any future editor screen). The
 * fab is intentionally fixed to the bottom-center of the viewport and
 * slides in via a Vue <Transition>; consumers only control its
 * `visible` state plus labels.
 */
interface Props {
  visible: boolean
  saving?: boolean
  saveLabel?: string
  discardLabel?: string
  badgeText?: string
}

withDefaults(defineProps<Props>(), {
  saving: false,
  saveLabel: '儲存',
  discardLabel: '放棄變更',
  badgeText: '尚未儲存',
})

const emit = defineEmits<{
  (e: 'save'): void
  (e: 'discard'): void
}>()

function onSave(): void {
  emit('save')
}

function onDiscard(): void {
  emit('discard')
}
</script>

<template>
  <Transition name="ag-fab">
    <div
      v-if="visible"
      class="ag-fab"
    >
      <el-tag
        type="warning"
        effect="light"
        round
      >
        {{ badgeText }}
      </el-tag>
      <el-button
        size="large"
        :disabled="saving"
        @click="onDiscard"
      >
        {{ discardLabel }}
      </el-button>
      <el-button
        type="success"
        size="large"
        :loading="saving"
        @click="onSave"
      >
        {{ saveLabel }}
      </el-button>
    </div>
  </Transition>
</template>

<style scoped>
.ag-fab {
  position: fixed;
  left: 50%;
  bottom: 24px;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  border-radius: 999px;
  background: var(--el-bg-color-overlay, #ffffff);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
  z-index: 100;
  max-width: calc(100vw - 24px);
  box-sizing: border-box;
}

/* Narrow phones: the pill-shaped layout with 3 "size=large" children can
   exceed a 375px viewport. Drop to a rounded rectangle that wraps and
   shrinks its buttons instead of clipping off-screen. */
@media (max-width: 767px) {
  .ag-fab {
    left: 12px;
    right: 12px;
    bottom: 12px;
    transform: none;
    max-width: none;
    border-radius: 16px;
    flex-wrap: wrap;
    justify-content: center;
    gap: 8px;
  }
  .ag-fab :deep(.el-button--large) {
    padding: 10px 16px;
    min-height: 44px;
  }
}

.ag-fab-enter-active,
.ag-fab-leave-active {
  transition: transform 0.22s ease, opacity 0.22s ease;
}
.ag-fab-enter-from,
.ag-fab-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(12px);
}

/* Mobile .ag-fab is no longer horizontally centered via transform (it's
   pinned via left/right instead) — the enter/leave slide must drop the
   matching translateX(-50%) or the fab visibly jumps sideways as the
   transition starts/ends. */
@media (max-width: 767px) {
  .ag-fab-enter-from,
  .ag-fab-leave-to {
    transform: translateY(12px);
  }
}
</style>
