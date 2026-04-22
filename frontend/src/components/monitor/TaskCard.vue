<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { TaskProgressEntry } from '@/types'
import { formatEta, formatRelative } from '@/utils/format'
import { cancelTask } from '@/api/client'

/** Card display variants — superset of TaskCategory for backward compat. */
type CardVariant = 'downloading' | 'waiting' | 'completed' | 'retry' | 'other'

const props = defineProps<{
  task: TaskProgressEntry
  variant: CardVariant
}>()

const borderColor = computed<string>(() => {
  if (props.variant === 'downloading') return 'var(--el-color-success)'
  if (props.variant === 'waiting') return 'var(--el-color-info)'
  if (props.variant === 'retry') return 'var(--el-color-danger)'
  return 'var(--el-color-info)'
})

const title = computed<string>(() => {
  if (props.task.bangumi_name) {
    const ep = props.task.episode ? ` - EP ${props.task.episode}` : ''
    return `《${props.task.bangumi_name}》${ep}`
  }
  return props.task.filename
})

const percentage = computed<number>(() =>
  Math.min(100, Math.max(0, Math.round(props.task.rate))),
)

const etaText = computed<string>(() => formatEta(props.task.eta_seconds))
const relativeTime = computed<string>(() => formatRelative(props.task.started_at))

const speedText = computed<string>(() => {
  if (props.task.speed_mbps == null) return ''
  return `${props.task.speed_mbps.toFixed(1)} MB/s`
})

// Reactive current time — updated every second so cooldown countdown ticks live.
const now = ref<number>(Date.now())
let _intervalId: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  _intervalId = setInterval(() => {
    now.value = Date.now()
  }, 1000)
})

onUnmounted(() => {
  if (_intervalId !== null) {
    clearInterval(_intervalId)
    _intervalId = null
  }
})

const cooldownRemainingText = computed<string>(() => {
  const raw = props.task.cooldown_until
  if (!raw) return ''
  const until = new Date(raw).getTime()
  const remaining = until - now.value
  if (remaining <= 0) return ''
  return `冷卻 ${Math.ceil(remaining / 1000)}s`
})

async function confirmCancel(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `確定要取消任務 sn=${props.task.sn} 嗎？`,
      '取消任務',
      {
        confirmButtonText: '確定取消',
        cancelButtonText: '保留',
        type: 'warning',
      },
    )
  } catch {
    // User dismissed the dialog — no action needed.
    return
  }

  try {
    await cancelTask(props.task.sn)
  } catch (err) {
    ElMessage.error(`取消失敗: ${err instanceof Error ? err.message : String(err)}`)
  }
}
</script>

<template>
  <div
    class="task-card"
    :style="{ borderLeftColor: borderColor }"
  >
    <div class="task-card__header">
      <el-tooltip
        :content="title"
        placement="top"
        :show-after="300"
      >
        <span class="task-card__title">{{ title }}</span>
      </el-tooltip>
      <span
        v-if="task.resolution"
        class="task-card__badge task-card__badge--resolution"
      >{{ task.resolution }}</span>
      <span
        v-if="task.retries && task.retries > 0"
        class="task-card__badge task-card__badge--retry"
      >重試 {{ task.retries }}</span>
      <el-button
        v-if="variant !== 'completed'"
        circle
        size="small"
        class="cancel-btn"
        title="取消任務"
        @click.stop="confirmCancel"
      >
        ✕
      </el-button>
    </div>

    <div class="task-card__status-row">
      <span class="task-card__status">{{ task.status }}</span>
      <span
        v-if="cooldownRemainingText"
        class="task-card__cooldown"
      >{{ cooldownRemainingText }}</span>
    </div>

    <el-progress
      :percentage="percentage"
      :stroke-width="8"
      :show-text="true"
      :format="(p: number) => `${p}%`"
      :status="variant === 'retry' ? 'exception' : undefined"
      class="task-card__progress"
    />

    <div class="task-card__footer">
      <span
        v-if="relativeTime"
        class="task-card__relative-time"
      >
        {{ relativeTime }}
      </span>
      <span class="task-card__metrics">
        <span
          v-if="speedText"
          class="task-card__speed"
        >{{ speedText }}</span>
        <span
          v-if="etaText && variant !== 'completed'"
          class="task-card__eta"
        >ETA {{ etaText }}</span>
      </span>
    </div>
  </div>
</template>

<style scoped>
.task-card {
  border-left: 4px solid var(--el-color-info);
  border-radius: 4px;
  background: var(--el-bg-color-overlay, #fff);
  padding: 10px 14px;
  margin-bottom: 8px;
  box-shadow: var(--el-box-shadow-lighter);
}

.task-card__header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}

.task-card__title {
  font-weight: 600;
  font-size: 0.9em;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-card__badge {
  font-size: 0.75em;
  padding: 1px 6px;
  border-radius: 10px;
  white-space: nowrap;
  flex-shrink: 0;
}

.task-card__badge--resolution {
  background: var(--el-fill-color-light);
  color: var(--el-text-color-secondary);
}

.task-card__badge--retry {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}

.task-card__status-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 0.85em;
}

.task-card__status {
  color: var(--el-text-color-secondary);
  /* Breathing room so the status text never crowds the el-progress percentage
     that sits at the right edge of the progress bar row just below. */
  margin-left: 8px;
}

.task-card__cooldown {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.task-card__progress {
  margin-bottom: 6px;
  /* Extra top gap separates the el-progress percentage text from the
     status / cooldown text row above, preventing visual crowding at
     narrow (3-column desktop) card widths. */
  margin-top: 4px;
}

.task-card__footer {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  font-size: 0.78em;
  color: var(--el-text-color-placeholder);
}

.task-card__relative-time {
  flex-shrink: 0;
}

.task-card__metrics {
  display: inline-flex;
  gap: 12px;
  align-items: baseline;
  flex-shrink: 0;
  margin-right: 8px;
}

.task-card__speed {
  font-size: 1em;
  color: var(--el-text-color-secondary);
}

.task-card__eta {
  font-size: 1em;
  color: var(--el-text-color-placeholder);
}

.cancel-btn {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 0.75em;
  opacity: 0.6;
  transition: opacity 0.15s;
}

.cancel-btn:hover {
  opacity: 1;
}
</style>
