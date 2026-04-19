/**
 * Log streaming WebSocket client.
 *
 * Mirrors the ProgressSocket pattern from ws.ts:
 *  - Exponential backoff reconnect
 *  - State ref (connecting / open / closed)
 *  - Injected socketFactory + location for tests
 */

import { ref, type Ref } from 'vue'

export interface LogEntry {
  timestamp: string
  level: string
  name: string
  message: string
  sn: number | null
}

export interface LogSocketOptions {
  path?: string
  /** Injected for tests; defaults to the global WebSocket. */
  socketFactory?: (url: string) => WebSocket
  /** Injected for tests; defaults to window.location. */
  location?: { protocol: string; host: string }
  /** Injected for tests; defaults to window.setTimeout / clearTimeout. */
  timerFactory?: {
    setTimeout: (fn: () => void, ms: number) => number
    clearTimeout: (id: number) => void
  }
  /** Called for each incoming log entry. */
  onMessage?: (entry: LogEntry) => void
}

export type SocketState = 'connecting' | 'open' | 'closed'

/** Exponential backoff delays in ms, capped at 30 s. */
const BACKOFF_DELAYS = [1000, 2000, 4000, 8000, 16000, 30000] as const

const DEFAULT_FACTORY: NonNullable<LogSocketOptions['socketFactory']> = (url) =>
  new WebSocket(url)

const DEFAULT_TIMER: NonNullable<LogSocketOptions['timerFactory']> = {
  setTimeout: (fn, ms) => window.setTimeout(fn, ms),
  clearTimeout: (id) => window.clearTimeout(id),
}

export class LogStreamSocket {
  readonly state: Ref<SocketState>
  readonly lines: Ref<LogEntry[]>

  private ws?: WebSocket
  private _userClosed = false
  private _backoffIndex = 0
  private _reconnectTimer?: number

  constructor(private readonly options: LogSocketOptions = {}) {
    this.state = ref<SocketState>('connecting')
    this.lines = ref<LogEntry[]>([])
  }

  private get _timer(): NonNullable<LogSocketOptions['timerFactory']> {
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
    const path = this.options.path ?? '/api/ws/logs'
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
    })

    this.ws.addEventListener('message', (ev) => {
      try {
        const entry = JSON.parse((ev as MessageEvent).data as string) as LogEntry
        this._addLine(entry)
        if (this.options.onMessage) {
          this.options.onMessage(entry)
        }
      } catch {
        // Ignore malformed frames.
      }
    })

    this.ws.addEventListener('close', () => {
      // Clear the reference so the next connect() call isn't blocked by the
      // idempotency guard (which would see the closed socket as still alive
      // when readyState is unavailable, e.g. in tests).
      this.ws = undefined
      if (!this._userClosed) {
        this.state.value = 'closed'
        this._scheduleReconnect()
      }
    })

    this.ws.addEventListener('error', () => {
      if (!this._userClosed) {
        this.state.value = 'closed'
        // Reconnect triggered by the subsequent close event.
      }
    })
  }

  close(): void {
    this._userClosed = true
    this._clearReconnectTimer()
    this.ws?.close()
    this.ws = undefined
  }

  /** Append *entry* to the lines buffer; trim to MAX_LINES. */
  private _addLine(entry: LogEntry): void {
    const MAX_LINES = 500
    this.lines.value = [...this.lines.value, entry].slice(-MAX_LINES)
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
