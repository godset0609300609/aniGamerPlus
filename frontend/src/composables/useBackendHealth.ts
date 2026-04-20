/**
 * Composable that polls GET /api/health every 10 s and tracks the overall
 * backend health state.
 *
 * Hysteresis rules (prevents flapping):
 *  - 2 consecutive failures  → transition to 'offline'
 *  - 2 consecutive degraded  → transition to 'degraded'
 *  - 1 success               → transition back to 'online'
 */

import { ref, type Ref } from 'vue'

export type HealthState = 'online' | 'degraded' | 'offline'

const POLL_INTERVAL_MS = 10_000
const FAILURE_THRESHOLD = 2
const DEGRADED_THRESHOLD = 2

export interface BackendHealth {
  state: Ref<HealthState>
  retryCount: Ref<number>
  lastCheckAt: Ref<number>
  start: () => void
  stop: () => void
  ping: () => Promise<void>
}

export function useBackendHealth(options?: {
  /** Injected for tests; defaults to window.fetch */
  fetchFn?: typeof fetch
  /** Injected for tests; defaults to window.setInterval / clearInterval */
  timerFactory?: {
    setInterval: (fn: () => void, ms: number) => number
    clearInterval: (id: number) => void
  }
}): BackendHealth {
  const fetchFn: typeof fetch = options?.fetchFn ?? ((input, init) => fetch(input, init))
  const timerFactory = options?.timerFactory ?? {
    setInterval: (fn, ms) => window.setInterval(fn, ms),
    clearInterval: (id) => window.clearInterval(id),
  }

  const state = ref<HealthState>('online')
  const retryCount = ref(0)
  const lastCheckAt = ref<number>(Date.now())

  let _failureCount = 0
  let _degradedCount = 0
  let _intervalId: number | undefined = undefined

  async function ping(): Promise<void> {
    lastCheckAt.value = Date.now()
    try {
      const resp = await fetchFn('/api/health')
      if (!resp.ok) {
        _onFailure()
        return
      }
      const data = (await resp.json()) as { status?: string }
      if (data.status === 'degraded') {
        _onDegraded()
      } else {
        _onSuccess()
      }
    } catch {
      _onFailure()
    }
  }

  function _onSuccess(): void {
    _failureCount = 0
    _degradedCount = 0
    retryCount.value = 0
    state.value = 'online'
  }

  function _onDegraded(): void {
    _failureCount = 0
    _degradedCount++
    retryCount.value = 0
    if (_degradedCount >= DEGRADED_THRESHOLD) {
      state.value = 'degraded'
    }
  }

  function _onFailure(): void {
    _degradedCount = 0
    _failureCount++
    retryCount.value = _failureCount
    if (_failureCount >= FAILURE_THRESHOLD) {
      state.value = 'offline'
    }
  }

  function start(): void {
    if (_intervalId !== undefined) return
    void ping()
    _intervalId = timerFactory.setInterval(() => void ping(), POLL_INTERVAL_MS)
  }

  function stop(): void {
    if (_intervalId !== undefined) {
      timerFactory.clearInterval(_intervalId)
      _intervalId = undefined
    }
  }

  return { state, retryCount, lastCheckAt, start, stop, ping }
}
