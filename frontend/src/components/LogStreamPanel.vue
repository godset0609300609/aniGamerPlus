<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { ElScrollbar } from 'element-plus'
import { LogStreamSocket } from '@/api/logs'
import type { LogEntry } from '@/api/logs'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------
const props = withDefaults(
  defineProps<{
    /**
     * When true: always connected and expanded — no collapse header shown,
     * fills the parent container height. Suitable for the dedicated /logs page.
     * When false (default): collapsed by default; socket only connects when open.
     */
    alwaysExpanded?: boolean
  }>(),
  { alwaysExpanded: false },
)

// ---------------------------------------------------------------------------
// Collapse state — collapsed by default; socket only connects when open.
// In alwaysExpanded mode the panel is always "open".
// ---------------------------------------------------------------------------
const activeNames = ref<string[]>([])
const isOpen = computed(() =>
  props.alwaysExpanded || activeNames.value.includes('logs'),
)

// ---------------------------------------------------------------------------
// Socket
// ---------------------------------------------------------------------------
const socket = new LogStreamSocket()

// When alwaysExpanded is true, connect immediately on mount.
if (props.alwaysExpanded) {
  socket.connect()
}

watch(isOpen, (open) => {
  if (open) {
    socket.connect()
  } else {
    // Only close if not in alwaysExpanded mode (alwaysExpanded stays open).
    if (!props.alwaysExpanded) {
      socket.close()
    }
  }
})

// Always close the socket when the component unmounts (e.g. route change).
// close() is idempotent so double-calling it (if isOpen watcher already ran
// socket.close()) is safe.
onBeforeUnmount(() => {
  socket.close()
})

// ---------------------------------------------------------------------------
// Filter state
// ---------------------------------------------------------------------------
const levelFilter = ref<string>('ALL')
const keywordFilter = ref<string>('')

const levelOptions = [
  { label: '全部', value: 'ALL' },
  { label: 'INFO', value: 'INFO' },
  { label: 'WARNING', value: 'WARNING' },
  { label: 'ERROR', value: 'ERROR' },
]

const filteredLines = computed((): LogEntry[] => {
  let lines = socket.lines.value
  if (levelFilter.value !== 'ALL') {
    lines = lines.filter((e) => e.level.toUpperCase() === levelFilter.value)
  }
  if (keywordFilter.value.trim()) {
    const kw = keywordFilter.value.trim().toLowerCase()
    lines = lines.filter((e) => e.message.toLowerCase().includes(kw))
  }
  return lines
})

// ---------------------------------------------------------------------------
// Auto-scroll — scroll the inner wrap element to the bottom whenever new
// lines are appended.
// ---------------------------------------------------------------------------
const scrollbarRef = ref<InstanceType<typeof ElScrollbar> | null>(null)

watch(
  () => filteredLines.value.length,
  () => {
    nextTick(() => {
      const el = scrollbarRef.value?.wrapRef
      if (el) {
        el.scrollTop = el.scrollHeight
      }
    })
  },
)

// ---------------------------------------------------------------------------
// Level colour helper
// ---------------------------------------------------------------------------
function levelClass(level: string): string {
  switch (level.toUpperCase()) {
    case 'WARNING':
      return 'log-level--warning'
    case 'ERROR':
    case 'CRITICAL':
      return 'log-level--error'
    case 'DEBUG':
      return 'log-level--debug'
    default:
      return 'log-level--info'
  }
}
</script>

<template>
  <!-- alwaysExpanded: skip the collapse shell, render content directly -->
  <div
    v-if="alwaysExpanded"
    class="log-stream-panel log-stream-panel--expanded"
  >
    <!-- Filter bar — fixed, never scrolled away -->
    <div class="log-stream-panel__filter">
      <el-select
        v-model="levelFilter"
        placeholder="等級"
        class="log-level-select"
      >
        <el-option
          v-for="opt in levelOptions"
          :key="opt.value"
          :label="opt.label"
          :value="opt.value"
        />
      </el-select>
      <el-input
        v-model="keywordFilter"
        placeholder="搜尋關鍵字…"
        clearable
        class="log-keyword-input"
      />
    </div>

    <!-- Log body — independent scrollbar, fills remaining height -->
    <el-scrollbar
      ref="scrollbarRef"
      class="log-stream-panel__body log-scrollbar"
    >
      <div
        v-for="(line, idx) in filteredLines"
        :key="idx"
        class="log-line"
        :class="levelClass(line.level)"
      >
        <span class="log-ts">{{ line.timestamp.slice(11, 19) }}</span>
        <span class="log-level">{{ line.level.slice(0, 4) }}</span>
        <span class="log-msg">{{ line.message }}</span>
      </div>
      <div
        v-if="filteredLines.length === 0"
        class="log-empty"
      >
        {{ socket.state.value === 'connecting' ? '連線中…' : '暫無日誌' }}
      </div>
    </el-scrollbar>
  </div>

  <!-- Normal collapsible mode -->
  <el-collapse
    v-else
    v-model="activeNames"
    class="log-stream-panel"
  >
    <el-collapse-item
      name="logs"
      title="系統日誌"
    >
      <!-- Toolbar -->
      <div class="log-toolbar">
        <el-select
          v-model="levelFilter"
          placeholder="等級"
          class="log-level-select"
        >
          <el-option
            v-for="opt in levelOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <el-input
          v-model="keywordFilter"
          placeholder="搜尋關鍵字…"
          clearable
          class="log-keyword-input"
        />
      </div>

      <!-- Log lines -->
      <el-scrollbar
        ref="scrollbarRef"
        max-height="300px"
        class="log-scrollbar"
      >
        <div
          v-for="(line, idx) in filteredLines"
          :key="idx"
          class="log-line"
          :class="levelClass(line.level)"
        >
          <span class="log-ts">{{ line.timestamp.slice(11, 19) }}</span>
          <span class="log-level">{{ line.level.slice(0, 4) }}</span>
          <span class="log-msg">{{ line.message }}</span>
        </div>
        <div
          v-if="filteredLines.length === 0 && isOpen"
          class="log-empty"
        >
          {{ socket.state.value === 'connecting' ? '連線中…' : '暫無日誌' }}
        </div>
      </el-scrollbar>
    </el-collapse-item>
  </el-collapse>
</template>

<style scoped>
.log-stream-panel {
  margin-top: 16px;
}

/*
 * In alwaysExpanded mode the parent (logs-shell__panel) is a flex child
 * with flex:1 and min-height:0.  We fill it completely and divide the
 * space into a fixed filter bar + a scrollable body.
 */
.log-stream-panel--expanded {
  display: flex;
  flex-direction: column;
  height: 100%;
  margin-top: 0;
  overflow: hidden;
}

/* ---------- Filter bar (never scrolled) ---------- */
.log-stream-panel__filter {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
  padding: 8px;
  border-bottom: 1px solid var(--el-border-color);
}

/* ---------- Log body (own scrollbar) ---------- */
.log-stream-panel__body {
  flex: 1;
  min-height: 0; /* required: flex child must opt out of intrinsic sizing */
}

/* el-scrollbar's inner wrap must fill the allocated height */
.log-stream-panel__body :deep(.el-scrollbar__wrap) {
  height: 100%;
}

/* ---------- Collapsible toolbar (non-expanded mode) ---------- */
.log-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.log-level-select {
  width: 110px;
  flex-shrink: 0;
}

.log-keyword-input {
  flex: 1;
}

.log-scrollbar {
  font-family: monospace;
  font-size: 12px;
  background: #1a1a1a;
  border-radius: 4px;
}

.log-line {
  display: flex;
  gap: 6px;
  padding: 2px 6px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
}

.log-ts {
  color: #666;
  flex-shrink: 0;
}

.log-level {
  flex-shrink: 0;
  width: 30px;
  font-weight: 600;
}

.log-msg {
  color: #ccc;
  flex: 1;
}

/* Level colours */
.log-level--info .log-level {
  color: #59abe3;
}

.log-level--warning .log-level {
  color: #f0ad4e;
}

.log-level--warning .log-msg {
  color: #f0ad4e;
}

.log-level--error .log-level {
  color: #e74c3c;
}

.log-level--error .log-msg {
  color: #e74c3c;
}

.log-level--debug .log-level {
  color: #aaa;
}

.log-empty {
  padding: 12px 8px;
  color: #555;
  text-align: center;
}
</style>
