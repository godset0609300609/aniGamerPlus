import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { LogStreamSocket } from '@/api/logs'
import type { LogEntry } from '@/api/logs'

// ---------------------------------------------------------------------------
// FakeSocket — minimal WebSocket double (mirrors ws.spec.ts)
// ---------------------------------------------------------------------------
class FakeSocket {
  listeners = new Map<string, ((ev: unknown) => void)[]>()
  close = vi.fn()
  addEventListener(event: string, cb: (ev: unknown) => void): void {
    const list = this.listeners.get(event) ?? []
    list.push(cb)
    this.listeners.set(event, list)
  }
  emit(event: string, ev: unknown = {}): void {
    for (const cb of this.listeners.get(event) ?? []) {
      cb(ev)
    }
  }
}

function fakeTimerFactory() {
  return {
    setTimeout: (fn: () => void, ms: number): number => setTimeout(fn, ms) as unknown as number,
    clearTimeout: (id: number) => clearTimeout(id),
  }
}

function makeSocket(overrides: Partial<ConstructorParameters<typeof LogStreamSocket>[0]> = {}) {
  const fake = new FakeSocket()
  const socket = new LogStreamSocket({
    location: { protocol: 'http:', host: 'localhost:5173' },
    socketFactory: () => fake as unknown as WebSocket,
    timerFactory: fakeTimerFactory(),
    ...overrides,
  })
  return { socket, fake }
}

function makeEntry(overrides: Partial<LogEntry> = {}): LogEntry {
  return {
    timestamp: '2024-01-01T00:00:00Z',
    level: 'INFO',
    name: 'test',
    message: 'hello',
    sn: null,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// URL building
// ---------------------------------------------------------------------------
describe('LogStreamSocket — URL building', () => {
  it('builds a ws:// URL from an http location', () => {
    let builtUrl = ''
    const fake = new FakeSocket()
    const socket = new LogStreamSocket({
      location: { protocol: 'http:', host: 'localhost:5173' },
      socketFactory: (url) => {
        builtUrl = url
        return fake as unknown as WebSocket
      },
    })
    socket.connect()
    expect(builtUrl).toBe('ws://localhost:5173/api/ws/logs')
  })

  it('upgrades to wss:// on an https location', () => {
    let builtUrl = ''
    const fake = new FakeSocket()
    new LogStreamSocket({
      location: { protocol: 'https:', host: 'example.com' },
      socketFactory: (url) => {
        builtUrl = url
        return fake as unknown as WebSocket
      },
    }).connect()
    expect(builtUrl).toBe('wss://example.com/api/ws/logs')
  })
})

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------
describe('LogStreamSocket — state transitions', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('starts in connecting state', () => {
    const { socket } = makeSocket()
    expect(socket.state.value).toBe('connecting')
  })

  it('transitions to open on socket open', () => {
    const { socket, fake } = makeSocket()
    socket.connect()
    fake.emit('open')
    expect(socket.state.value).toBe('open')
  })

  it('transitions to closed on socket close', () => {
    const { socket, fake } = makeSocket()
    socket.connect()
    fake.emit('open')
    fake.emit('close')
    expect(socket.state.value).toBe('closed')
  })

  it('reconnects after backoff delay', () => {
    const instances: FakeSocket[] = []
    const socket = new LogStreamSocket({
      location: { protocol: 'http:', host: 'x' },
      socketFactory: () => {
        const f = new FakeSocket()
        instances.push(f)
        return f as unknown as WebSocket
      },
      timerFactory: fakeTimerFactory(),
    })
    socket.connect()
    instances[0].emit('open')
    instances[0].emit('close')
    expect(socket.state.value).toBe('closed')

    vi.advanceTimersByTime(1001)
    expect(socket.state.value).toBe('connecting')
    socket.close()
  })
})

// ---------------------------------------------------------------------------
// Messages — lines buffer
// ---------------------------------------------------------------------------
describe('LogStreamSocket — lines buffer', () => {
  it('appends incoming entries to lines', () => {
    const { socket, fake } = makeSocket()
    socket.connect()
    fake.emit('open')

    const entry = makeEntry({ message: 'first' })
    fake.emit('message', { data: JSON.stringify(entry) })

    expect(socket.lines.value).toHaveLength(1)
    expect(socket.lines.value[0].message).toBe('first')
  })

  it('ignores malformed frames', () => {
    const { socket, fake } = makeSocket()
    socket.connect()
    fake.emit('open')
    fake.emit('message', { data: 'not-json' })
    expect(socket.lines.value).toHaveLength(0)
  })

  it('trims to 500 entries maximum', () => {
    const { socket, fake } = makeSocket()
    socket.connect()
    fake.emit('open')
    for (let i = 0; i < 510; i++) {
      fake.emit('message', { data: JSON.stringify(makeEntry({ message: `msg-${i}` })) })
    }
    expect(socket.lines.value).toHaveLength(500)
    // Oldest entries trimmed — only the last 500 remain
    expect(socket.lines.value[0].message).toBe('msg-10')
    expect(socket.lines.value[499].message).toBe('msg-509')
  })

  it('calls onMessage callback for each entry', () => {
    const onMessage = vi.fn()
    const fake = new FakeSocket()
    const socket = new LogStreamSocket({
      location: { protocol: 'http:', host: 'x' },
      socketFactory: () => fake as unknown as WebSocket,
      onMessage,
    })
    socket.connect()
    fake.emit('open')

    const entry = makeEntry({ level: 'WARNING' })
    fake.emit('message', { data: JSON.stringify(entry) })

    expect(onMessage).toHaveBeenCalledWith(entry)
  })
})

// ---------------------------------------------------------------------------
// close() — no reconnect after user close
// ---------------------------------------------------------------------------
describe('LogStreamSocket — close() prevents reconnect', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('does not reconnect after user calls close()', () => {
    const instances: FakeSocket[] = []
    const socket = new LogStreamSocket({
      location: { protocol: 'http:', host: 'x' },
      socketFactory: () => {
        const f = new FakeSocket()
        instances.push(f)
        return f as unknown as WebSocket
      },
      timerFactory: fakeTimerFactory(),
    })
    socket.connect()
    socket.close()
    instances[0].emit('close')

    vi.advanceTimersByTime(60_000)
    expect(instances.length).toBe(1)
  })
})

// ---------------------------------------------------------------------------
// connect() idempotency
// ---------------------------------------------------------------------------
describe('LogStreamSocket — connect() idempotency', () => {
  it('test_connect_is_idempotent_when_already_open: calling connect() twice creates only one WebSocket', () => {
    const instances: FakeSocket[] = []
    const socket = new LogStreamSocket({
      location: { protocol: 'http:', host: 'x' },
      socketFactory: () => {
        const f = new FakeSocket()
        instances.push(f)
        // Simulate an already-open socket: readyState === 1 (OPEN).
        Object.defineProperty(f, 'readyState', { get: () => 1 })
        return f as unknown as WebSocket
      },
    })

    // First connect — creates the WebSocket.
    socket.connect()
    expect(instances).toHaveLength(1)

    // Second connect — socket is already open, should be a no-op.
    socket.connect()
    expect(instances).toHaveLength(1)

    socket.close()
  })
})

// ---------------------------------------------------------------------------
// Exponential backoff
// ---------------------------------------------------------------------------
describe('LogStreamSocket — exponential backoff', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('reconnects with 1/2/4/8/16/30/30 s backoff', () => {
    const instances: FakeSocket[] = []
    let connectCount = 0

    const socket = new LogStreamSocket({
      location: { protocol: 'http:', host: 'x' },
      socketFactory: () => {
        connectCount++
        const f = new FakeSocket()
        instances.push(f)
        return f as unknown as WebSocket
      },
      timerFactory: fakeTimerFactory(),
    })

    socket.connect()
    const expected = [1000, 2000, 4000, 8000, 16000, 30000, 30000]

    for (let i = 0; i < expected.length; i++) {
      instances[i].emit('close')
      vi.advanceTimersByTime(expected[i] - 1)
      expect(connectCount).toBe(i + 1)
      vi.advanceTimersByTime(2)
      expect(connectCount).toBe(i + 2)
    }

    socket.close()
  })
})
