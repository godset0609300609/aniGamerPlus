/**
 * Composable that manages the Telegram binding state and polling for a user.
 *
 * Encapsulates:
 * - Starting a link (POST start-link) and opening the t.me URL
 * - Polling GET /status every 3 s while a link is pending
 * - Unlinking (POST unlink)
 * - Toggling notifications (PATCH notify-enabled)
 * - Countdown display for the pending-link expiry
 */

import { computed, ref } from 'vue'

export interface TelegramStatus {
  bound: boolean
  chat_id: number | null
  enabled: boolean
  link_pending: boolean
  link_expires_in_seconds: number | null
}

export interface StartLinkResponse {
  link_url: string
  expires_in_seconds: number
}

const POLL_INTERVAL_MS = 3_000

export interface UseTelegramBindingOptions {
  /** Injected for tests; defaults to window.fetch */
  fetchFn?: typeof fetch
  /** Injected for tests; defaults to window.setInterval / clearInterval */
  timerFactory?: {
    setInterval: (fn: () => void, ms: number) => number
    clearInterval: (id: number) => void
  }
  /** Injected for tests; defaults to window.open */
  openFn?: (url: string, target: string, features: string) => void
  /** Injected for tests; defaults to Date.now */
  nowFn?: () => number
}

export function useTelegramBinding(options?: UseTelegramBindingOptions) {
  const fetchFn: typeof fetch = options?.fetchFn ?? fetch
  const timerFactory = options?.timerFactory ?? {
    setInterval: (fn, ms) => window.setInterval(fn, ms),
    clearInterval: (id) => window.clearInterval(id),
  }
  const openFn =
    options?.openFn ??
    ((url: string, target: string, features: string) => window.open(url, target, features))
  const nowFn = options?.nowFn ?? (() => Date.now())

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------
  const bound = ref(false)
  const notifyEnabled = ref(true)
  const linkPending = ref(false)
  const notConfigured = ref(false)
  const loading = ref(false)
  const error = ref<string | null>(null)

  // Countdown state for pending link.
  const linkExpiresAt = ref<number | null>(null)
  const secondsRemaining = ref<number>(0)

  let _pollId: number | undefined = undefined
  let _countdownId: number | undefined = undefined

  // ---------------------------------------------------------------------------
  // Derived
  // ---------------------------------------------------------------------------
  const countdownLabel = computed<string>(() => {
    const s = secondsRemaining.value
    if (s <= 0) return '0:00'
    const m = Math.floor(s / 60)
    const sec = s % 60
    return `${m}:${sec.toString().padStart(2, '0')}`
  })

  // ---------------------------------------------------------------------------
  // Internal helpers
  // ---------------------------------------------------------------------------

  function _stopPoll(): void {
    if (_pollId !== undefined) {
      timerFactory.clearInterval(_pollId)
      _pollId = undefined
    }
  }

  function _stopCountdown(): void {
    if (_countdownId !== undefined) {
      timerFactory.clearInterval(_countdownId)
      _countdownId = undefined
    }
    secondsRemaining.value = 0
  }

  function _startCountdown(expiresAt: number): void {
    _stopCountdown()
    linkExpiresAt.value = expiresAt
    const _tick = () => {
      const remaining = Math.max(0, Math.floor((linkExpiresAt.value! - nowFn()) / 1000))
      secondsRemaining.value = remaining
      if (remaining <= 0) {
        _stopCountdown()
      }
    }
    _tick()
    _countdownId = timerFactory.setInterval(_tick, 1000)
  }

  async function _apiFetch<T>(
    path: string,
    options?: {
      method?: string
      body?: unknown
    },
  ): Promise<T> {
    const init: RequestInit = {
      credentials: 'include',
      method: options?.method ?? (options?.body !== undefined ? 'POST' : 'GET'),
    }
    if (options?.body !== undefined) {
      init.headers = { 'Content-Type': 'application/json' }
      init.body = JSON.stringify(options.body)
    }
    const res = await fetchFn(path, init)
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      let detail = ''
      try {
        detail = (JSON.parse(text) as { detail?: string }).detail ?? text
      } catch {
        detail = text
      }
      const err = new Error(detail || `HTTP ${res.status}`)
      ;(err as Error & { status?: number }).status = res.status
      throw err
    }
    return (await res.json()) as T
  }

  // ---------------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------------

  async function loadStatus(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const data = await _apiFetch<TelegramStatus>('/api/profile/telegram/status')
      bound.value = data.bound
      notifyEnabled.value = data.enabled
      if (data.link_pending && data.link_expires_in_seconds != null && data.link_expires_in_seconds > 0) {
        linkPending.value = true
        const expiresAt = nowFn() + data.link_expires_in_seconds * 1000
        _startCountdown(expiresAt)
        _startPolling()
      } else {
        // Not pending, already expired server-side, or missing expiry — clear pending state.
        linkPending.value = false
        _stopPoll()
        _stopCountdown()
      }
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loading.value = false
    }
  }

  async function startLink(): Promise<void> {
    loading.value = true
    error.value = null
    notConfigured.value = false
    try {
      const data = await _apiFetch<StartLinkResponse>('/api/profile/telegram/start-link', {
        method: 'POST',
      })
      openFn(data.link_url, '_blank', 'noopener,noreferrer')
      linkPending.value = true
      const expiresAt = nowFn() + data.expires_in_seconds * 1000
      _startCountdown(expiresAt)
      _startPolling()
    } catch (e) {
      const detail = (e as Error).message
      if (detail === 'telegram_not_configured') {
        notConfigured.value = true
      } else {
        error.value = detail
      }
    } finally {
      loading.value = false
    }
  }

  function _startPolling(): void {
    _stopPoll()
    _pollId = timerFactory.setInterval(() => {
      void _pollStatus()
    }, POLL_INTERVAL_MS)
  }

  async function _pollStatus(): Promise<void> {
    try {
      const data = await _apiFetch<TelegramStatus>('/api/profile/telegram/status')
      if (data.bound) {
        bound.value = true
        notifyEnabled.value = data.enabled
        linkPending.value = false
        _stopPoll()
        _stopCountdown()
      } else if (!data.link_pending) {
        // Token expired server-side.
        linkPending.value = false
        _stopPoll()
        _stopCountdown()
      }
    } catch {
      // Best-effort polling — swallow errors.
    }
  }

  async function unlink(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      await _apiFetch('/api/profile/telegram/unlink', { method: 'POST' })
      bound.value = false
      linkPending.value = false
      notifyEnabled.value = true
      _stopPoll()
      _stopCountdown()
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loading.value = false
    }
  }

  async function setNotifyEnabled(value: boolean): Promise<void> {
    try {
      await _apiFetch('/api/profile/telegram/notify-enabled', {
        method: 'PATCH',
        body: { enabled: value },
      })
      notifyEnabled.value = value
    } catch (e) {
      error.value = (e as Error).message
    }
  }

  function dispose(): void {
    _stopPoll()
    _stopCountdown()
  }

  return {
    bound,
    notifyEnabled,
    linkPending,
    notConfigured,
    loading,
    error,
    countdownLabel,
    secondsRemaining,
    loadStatus,
    startLink,
    unlink,
    setNotifyEnabled,
    dispose,
  }
}
