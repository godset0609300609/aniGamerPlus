import { onMounted, onUnmounted } from 'vue'

/**
 * Polls `refetchFn` on a fixed interval while the tab is visible, and also
 * refetches immediately whenever the tab regains visibility (so a user who
 * tabs away and back doesn't wait out a stale interval).
 *
 * Used by the BT/TG record/history tabs (EntriesTab, FeedsTab, FiltersTab,
 * DownloadsTab, ChatsTab) so a user parked on one of these views while a
 * download is in flight sees it progress without a manual refresh.
 * Deliberately NOT wired into MonitorView — that view is already
 * WebSocket-live and doesn't need polling.
 */
export function useAutoRefresh(intervalMs: number, refetchFn: () => void | Promise<void>): void {
  let timer: ReturnType<typeof setInterval> | null = null

  function start(): void {
    if (timer !== null) return
    timer = setInterval(() => {
      if (document.visibilityState === 'visible') void refetchFn()
    }, intervalMs)
  }

  function stop(): void {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  function onVisibility(): void {
    if (document.visibilityState === 'visible') void refetchFn()
  }

  onMounted(() => {
    start()
    document.addEventListener('visibilitychange', onVisibility)
  })

  onUnmounted(() => {
    stop()
    document.removeEventListener('visibilitychange', onVisibility)
  })
}
