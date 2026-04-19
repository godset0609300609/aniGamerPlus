import { computed, ref, type ComputedRef, type Ref } from 'vue'
import type { TaskProgressMap } from '@/types'

export interface ProgressSocketHandlers {
  onMessage: (tasks: TaskProgressMap) => void
  onClose?: (event: CloseEvent) => void
  onError?: (event: Event) => void
}

export interface ProgressSocketOptions {
  path?: string
  /** Injected for tests; in production this defaults to the global WebSocket. */
  socketFactory?: (url: string) => WebSocket
  /** Injected for tests; defaults to window.location. */
  location?: { protocol: string; host: string }
  /** Injected for tests; defaults to window.setTimeout / clearTimeout. */
  timerFactory?: {
    setTimeout: (fn: () => void, ms: number) => number
    clearTimeout: (id: number) => void
  }
}

export type SocketState = 'connecting' | 'open' | 'closed'

/** Exponential backoff delays in ms, capped at 30 s. */
const BACKOFF_DELAYS = [1000, 2000, 4000, 8000, 16000, 30000] as const

/** Grace period before the disconnect banner appears (ms). */
const DISCONNECT_GRACE_MS = 3000

const DEFAULT_FACTORY: NonNullable<ProgressSocketOptions['socketFactory']> = (url) =>
  new WebSocket(url)

const DEFAULT_TIMER: NonNullable<ProgressSocketOptions['timerFactory']> = {
  setTimeout: (fn, ms) => window.setTimeout(fn, ms),
  clearTimeout: (id) => window.clearTimeout(id),
}

export class ProgressSocket {
  readonly state: Ref<SocketState>
  /** True after 3 s of continuous disconnect — drives the warning banner. */
  readonly showDisconnectedBanner: Ref<boolean>
  /** The last non-empty task snapshot — kept alive for the dimmed UI. */
  readonly lastTasks: Ref<TaskProgressMap>
  /** Whether the socket has ever received any tasks message. */
  readonly hasReceivedFirst: Ref<boolean>
  /** Convenience computed: banner visibility. */
  readonly isDisconnectedLongEnough: ComputedRef<boolean>

  private ws?: WebSocket
  private _userClosed = false
  private _backoffIndex = 0
  private _reconnectTimer?: number
  private _graceTimer?: number

  constructor(
    private readonly handlers: ProgressSocketHandlers,
    private readonly options: ProgressSocketOptions = {},
  ) {
    this.state = ref<SocketState>('connecting')
    this.showDisconnectedBanner = ref(false)
    this.lastTasks = ref<TaskProgressMap>({})
    this.hasReceivedFirst = ref(false)
    this.isDisconnectedLongEnough = computed(() => this.showDisconnectedBanner.value)
  }

  private get _timer(): NonNullable<ProgressSocketOptions['timerFactory']> {
    return this.options.timerFactory ?? DEFAULT_TIMER
  }

  connect(): void {
    if (this._userClosed) return
    // If the socket is already open or connecting, do nothing.
    // Use the numeric constant (3 = CLOSED) to avoid referencing the global
    // WebSocket constructor (which may be undefined in test environments).
    if (this.ws !== undefined && this.ws.readyState !== 3 /* CLOSED */) {
      return
    }

    const loc = this.options.location ?? window.location
    const wsProto = loc.protocol === 'https:' ? 'wss:' : 'ws:'
    const path = this.options.path ?? '/api/ws/tasks_progress'
    const factory = this.options.socketFactory ?? DEFAULT_FACTORY
    const url = `${wsProto}//${loc.host}${path}`

    this.state.value = 'connecting'
    this.ws = factory(url)

    this.ws.addEventListener('open', () => {
      if (this._userClosed) {
        this.ws?.close()
        return
      }
      this._backoffIndex = 0
      this.state.value = 'open'
      this._clearGraceTimer()
      this.showDisconnectedBanner.value = false
    })

    this.ws.addEventListener('message', (ev) => {
      try {
        const parsed = JSON.parse((ev as MessageEvent).data as string) as TaskProgressMap
        this.handlers.onMessage(parsed)
        this.hasReceivedFirst.value = true
        // Keep the last non-empty snapshot for the dimmed overlay.
        if (Object.keys(parsed).length > 0) {
          this.lastTasks.value = parsed
        }
      } catch {
        // Ignore malformed frames — the backend never sends them but be defensive.
      }
    })

    this.ws.addEventListener('close', (ev) => {
      // Clear the reference so the next connect() call isn't blocked by the
      // idempotency guard (which would see the closed socket as still alive
      // when readyState is unavailable, e.g. in tests).
      this.ws = undefined
      if (!this._userClosed) {
        this.state.value = 'closed'
        this._startGraceTimer()
        this._scheduleReconnect()
      }
      if (this.handlers.onClose) {
        this.handlers.onClose(ev as CloseEvent)
      }
    })

    this.ws.addEventListener('error', (ev) => {
      if (!this._userClosed) {
        this.state.value = 'closed'
        this._startGraceTimer()
        // Reconnect is triggered by the subsequent close event.
      }
      if (this.handlers.onError) {
        this.handlers.onError(ev)
      }
    })
  }

  close(): void {
    this._userClosed = true
    this._clearGraceTimer()
    this._clearReconnectTimer()
    this.ws?.close()
    this.ws = undefined
  }

  private _startGraceTimer(): void {
    this._clearGraceTimer()
    this._graceTimer = this._timer.setTimeout(() => {
      // Show the banner if still not connected (covers both 'closed' and
      // 'connecting' — the latter means a reconnect attempt is in progress).
      if (this.state.value !== 'open') {
        this.showDisconnectedBanner.value = true
      }
    }, DISCONNECT_GRACE_MS)
  }

  private _clearGraceTimer(): void {
    if (this._graceTimer !== undefined) {
      this._timer.clearTimeout(this._graceTimer)
      this._graceTimer = undefined
    }
  }

  private _scheduleReconnect(): void {
    this._clearReconnectTimer()
    const delay = BACKOFF_DELAYS[Math.min(this._backoffIndex, BACKOFF_DELAYS.length - 1)]
    this._backoffIndex++
    this._reconnectTimer = this._timer.setTimeout(() => {
      this._reconnectTimer = undefined
      if (!this._userClosed) {
        this.connect()
      }
    }, delay)
  }

  private _clearReconnectTimer(): void {
    if (this._reconnectTimer !== undefined) {
      this._timer.clearTimeout(this._reconnectTimer)
      this._reconnectTimer = undefined
    }
  }
}
