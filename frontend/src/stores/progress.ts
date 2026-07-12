/**
 * Progress store — ref-based singleton that owns the single app-scope
 * WebSocket connection for task progress updates.
 *
 * Pattern mirrors stores/auth.ts: module-level lazy singleton, no Pinia.
 *
 * Usage:
 *   const store = useProgressStore()
 *   store.connect()          // call once (e.g. in App.vue onMounted)
 *   store.tasks.value        // reactive TaskProgressMap
 *   store.byCategory.value   // { downloading, waiting, completed }
 *
 * History
 * -------
 * In addition to the live WS tasks, the store polls GET /api/tasks/history
 * every 60 s to surface completed records from the DB (survives scheduler
 * restarts).  ``historyEntries`` and the ``completed`` bucket in
 * ``byCategory`` merge live in-memory terminal tasks + DB history so the
 * UI always shows the full 7-day picture.
 */

import { computed, ref, type ComputedRef, type Ref } from 'vue'
import { ProgressSocket } from '@/api/ws'
import { TasksApi } from '@/api/tasks'
import { categorize, isWithinLastNDays } from '@/composables/useTaskCategory'
import type { TaskProgressMap, TaskProgressEntry, TaskHistoryEntry } from '@/types'
import type { SocketState } from '@/api/ws'

// ---------------------------------------------------------------------------
// Terminal statuses — tasks with these statuses are excluded from active
// entries and counts.  Kept here so MonitorView can import the same set.
// ---------------------------------------------------------------------------
export const TERMINAL_STATUSES = new Set(['下載完成', '上傳完成', '任務完成', '失敗'])

// ---------------------------------------------------------------------------
// Hidden statuses — tasks with these statuses are completely hidden from the
// monitor UI (neither active nor completed columns).  DB records are kept for
// audit purposes; they are just not surfaced in the frontend.
//
// SYNC NOTE: When adding a status here, also add it to list_recent() in
// backend/app/persistence/task_history_repo.py (the NOT IN clause).
// ---------------------------------------------------------------------------
export const HIDDEN_FROM_MONITOR = new Set(['已取消', '中斷'])

const COMPLETED_DAYS = 7
const HISTORY_POLL_INTERVAL_MS = 60_000

const RETRY_STATUSES = new Set(['任務失敗, 等待重啓', '失敗! 重啓中'])

// ---------------------------------------------------------------------------
// Store shape
// ---------------------------------------------------------------------------
export interface ProgressStore {
  tasks: Ref<TaskProgressMap>
  state: Ref<SocketState>
  showDisconnectedBanner: Ref<boolean>
  lastTasks: Ref<TaskProgressMap>
  hasReceivedFirst: Ref<boolean>
  /** All non-terminal entries, sorted by descending sn. */
  activeEntries: ComputedRef<TaskProgressEntry[]>
  /** Terminal entries within the last 7 days, sorted by descending sn. */
  completedEntries: ComputedRef<TaskProgressEntry[]>
  /** DB-persisted history entries (completed + interrupted), refreshed every 60 s. */
  historyEntries: Ref<TaskHistoryEntry[]>
  downloadingCount: ComputedRef<number>
  waitingCount: ComputedRef<number>
  /** Count of retry-status tasks (subset of downloading column). Used by HeaderTaskIndicator. */
  retryCount: ComputedRef<number>
  completedCount: ComputedRef<number>
  totalCount: ComputedRef<number>
  byCategory: ComputedRef<{
    downloading: TaskProgressEntry[]
    waiting: TaskProgressEntry[]
    completed: TaskProgressEntry[]
  }>
  connect(): void
  close(): void
  /** Manually refresh history from the DB. Automatically called on connect + every 60 s. */
  loadHistory(days?: number): Promise<void>
}

// ---------------------------------------------------------------------------
// Module-level singleton
// ---------------------------------------------------------------------------
let _instance: ProgressStore | null = null

function buildStore(): ProgressStore {
  const tasks = ref<TaskProgressMap>({})
  const historyEntries = ref<TaskHistoryEntry[]>([])
  const tasksApi = new TasksApi()
  let _historyPollTimer: ReturnType<typeof setInterval> | null = null

  const socket = new ProgressSocket({
    onMessage: (payload: TaskProgressMap) => {
      tasks.value = payload
    },
  })

  const activeEntries = computed((): TaskProgressEntry[] =>
    Object.entries(tasks.value)
      .filter(
        ([, entry]) =>
          !TERMINAL_STATUSES.has(entry.status) && !HIDDEN_FROM_MONITOR.has(entry.status),
      )
      .sort(([a], [b]) => Number(b) - Number(a))
      .map(([, entry]) => entry),
  )

  const completedEntries = computed((): TaskProgressEntry[] =>
    Object.entries(tasks.value)
      .filter(([, entry]) => {
        if (!TERMINAL_STATUSES.has(entry.status)) return false
        if (HIDDEN_FROM_MONITOR.has(entry.status)) return false
        // Prefer finished_at (set by ProgressBus.finish); fall back to
        // started_at for entries that pre-date the finished_at field.
        const when = entry.finished_at ?? entry.started_at ?? null
        return isWithinLastNDays(when, COMPLETED_DAYS)
      })
      .sort(([a], [b]) => Number(b) - Number(a))
      .map(([, entry]) => entry),
  )

  // ---------------------------------------------------------------------------
  // Sort helper: finished_at descending (ISO-8601 strings compare correctly
  // via localeCompare; null/undefined entries sink to the bottom).
  // ---------------------------------------------------------------------------
  function compareFinishedDesc(
    a: TaskProgressEntry | TaskHistoryEntry,
    b: TaskProgressEntry | TaskHistoryEntry,
  ): number {
    const aT = a.finished_at ?? null
    const bT = b.finished_at ?? null
    if (aT === null && bT === null) return 0
    if (aT === null) return 1 // no timestamp → sink to bottom
    if (bT === null) return -1
    return bT.localeCompare(aT) // ISO-8601 descending == newest first
  }

  /**
   * Merge live completed (in-memory) and DB history entries into the
   * completed column.  Each unique (sn, started_at) pair is its own card,
   * so multiple genuine download attempts for the same sn (e.g. a manual
   * re-download after the first attempt already finished — each attempt
   * gets its own `task_history` row, see
   * backend/app/persistence/task_history_repo.py) each appear separately.
   * Live entries take precedence over DB history for the same attempt.
   *
   * Matching rules:
   *  - Live entry has a real `started_at`: matched against a DB-history
   *    row for the same sn only when `started_at` is identical (exact
   *    per-attempt match). This is what lets multiple genuine re-download
   *    attempts for the same sn each surface as their own card.
   *  - Live entry has `started_at === null`: this only happens for
   *    boot-time ghost-reconciliation entries synthesised by
   *    `ProgressBus.force_finish` (backend/app/downloader/progress.py),
   *    which closes out a stuck entry this process never locally `start()`ed
   *    and so never knows the real started_at. Matching those strictly by
   *    "sn|started_at" never matches the DB-history row's real started_at,
   *    so the same completed TG/BT task rendered as two cards. Matched by
   *    sn alone instead — a null-started_at live entry and its DB-history
   *    counterpart are always the same attempt, never a distinct one.
   */
  const mergedCompleted = computed((): TaskProgressEntry[] => {
    // Live completed attempts with a known started_at: matched exactly by "sn|started_at".
    const liveKeys = new Set<string>()
    // Live completed attempts with started_at === null (force_finish ghost
    // reconciliation entries): matched by sn alone, regardless of the DB
    // row's started_at, so they collapse into a single card.
    const liveGhostSns = new Set<number>()
    for (const entry of completedEntries.value) {
      if (entry.started_at) {
        liveKeys.add(`${entry.sn}|${entry.started_at}`)
      } else {
        liveGhostSns.add(entry.sn)
      }
    }

    // Add history rows not already covered by a live entry (either an exact
    // sn|started_at match, or a ghost sn-only match), excluding statuses
    // that should be hidden from the monitor UI.
    const historyAsProgressEntries: TaskProgressEntry[] = historyEntries.value
      .filter(
        (h) =>
          !liveGhostSns.has(h.sn) &&
          !liveKeys.has(`${h.sn}|${h.started_at ?? ''}`) &&
          !HIDDEN_FROM_MONITOR.has(h.final_status),
      )
      .map(
        (h): TaskProgressEntry => ({
          sn: h.sn,
          rate: 100,
          status: h.final_status,
          filename: h.filename,
          bangumi_name: h.bangumi_name,
          episode: h.episode,
          resolution: h.resolution,
          retries: h.retries,
          started_at: h.started_at,
          finished_at: h.finished_at,
          owner_id: h.owner_id,
          // Regression guard: omitting this dropped every DB-history-derived
          // completed card into sourceBadge.ts's null fallback, which used
          // to render as a mislabeled 動畫瘋 badge regardless of the row's
          // real source. See TaskHistoryEntryOut.source in app/models.py —
          // the backend already populates it, this was just lost in transit.
          source: h.source,
          external_id: h.external_id,
        }),
      )

    return [...completedEntries.value, ...historyAsProgressEntries].sort(compareFinishedDesc)
  })

  const byCategory = computed(() => {
    const result = {
      downloading: [] as TaskProgressEntry[],
      waiting: [] as TaskProgressEntry[],
      completed: [] as TaskProgressEntry[],
    }
    for (const entry of activeEntries.value) {
      const cat = categorize(entry.status)
      if (cat === 'waiting') {
        result.waiting.push(entry)
      } else {
        // 'downloading', 'other' (and retry statuses) land here.
        result.downloading.push(entry)
      }
    }
    result.completed = mergedCompleted.value
    return result
  })

  const downloadingCount = computed(() => byCategory.value.downloading.length)
  const waitingCount = computed(() => byCategory.value.waiting.length)
  const retryCount = computed(
    () => activeEntries.value.filter((e) => RETRY_STATUSES.has(e.status)).length,
  )
  const completedCount = computed(() => byCategory.value.completed.length)
  const totalCount = computed(() => downloadingCount.value + waitingCount.value)

  async function loadHistory(days: number = COMPLETED_DAYS): Promise<void> {
    try {
      historyEntries.value = await tasksApi.fetchHistory(days)
    } catch {
      // History fetch failures are non-fatal; keep current historyEntries.
    }
  }

  function connect(): void {
    socket.connect()
    // Fetch history immediately on connect, then poll every 60 s.
    void loadHistory()
    if (_historyPollTimer === null) {
      _historyPollTimer = setInterval(() => void loadHistory(), HISTORY_POLL_INTERVAL_MS)
    }
  }

  function close(): void {
    socket.close()
    if (_historyPollTimer !== null) {
      clearInterval(_historyPollTimer)
      _historyPollTimer = null
    }
  }

  return {
    tasks,
    state: socket.state,
    showDisconnectedBanner: socket.showDisconnectedBanner,
    lastTasks: socket.lastTasks,
    hasReceivedFirst: socket.hasReceivedFirst,
    activeEntries,
    completedEntries,
    historyEntries,
    downloadingCount,
    waitingCount,
    retryCount,
    completedCount,
    totalCount,
    byCategory,
    connect,
    close,
    loadHistory,
  }
}

export function useProgressStore(): ProgressStore {
  if (_instance) return _instance
  _instance = buildStore()
  return _instance
}

/** Test hook — reset singleton so each test gets a fresh store + socket. */
export function __resetProgressStoreForTest(): void {
  _instance = null
}
