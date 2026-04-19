import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { ProgressSocket } from '@/api/ws'

// ---------------------------------------------------------------------------
// FakeSocket — minimal WebSocket double.
// ---------------------------------------------------------------------------
class FakeSocket {
  listeners = new Map<string, ((ev: unknown) => void)[]>()
  close = vi.fn()
  addEventListener(event: string, cb: (ev: unknown) => void): void {
    const list = this.listeners.get(event) ?? []
    list.push(cb)
    this.listeners.set(event, list)
  }
  emit(event: string, ev: unknown): void {
    for (const cb of this.listeners.get(event) ?? []) {
      cb(ev)
    }
  }
}

/** Creates a controllable timer factory backed by vi fake timers. */
function fakeTimerFactory() {
  return {
    setTimeout: (fn: () => void, ms: number): number => setTimeout(fn, ms) as unknown as number,
    clearTimeout: (id: number) => clearTimeout(id),
  }
}

/** Builds a ProgressSocket wired to a FakeSocket with fake timers. */
function makeSocket(overrides: Partial<ConstructorParameters<typeof ProgressSocket>[1]> = {}) {
  const fake = new FakeSocket()
  const socket = new ProgressSocket(
    { onMessage: vi.fn() },
    {
      location: { protocol: 'http:', host: 'localhost:5173' },
      socketFactory: () => fake as unknown as WebSocket,
      timerFactory: fakeTimerFactory(),
      ...overrides,
    },
  )
  return { socket, fake }
}

// ---------------------------------------------------------------------------
// Existing URL / message / close tests (preserved, backward-compat)
// ---------------------------------------------------------------------------
describe('ProgressSocket — URL building', () => {
  it('builds a ws:// URL from an http location', () => {
    let builtUrl = ''
    const fake = new FakeSocket()
    const socket = new ProgressSocket(
      { onMessage: () => undefined },
      {
        location: { protocol: 'http:', host: 'localhost:5173' },
        socketFactory: (url) => {
          builtUrl = url
          return fake as unknown as WebSocket
        },
      },
    )
    socket.connect()
    expect(builtUrl).toBe('ws://localhost:5173/api/ws/tasks_progress')
  })

  it('upgrades to wss:// on an https location', () => {
    let builtUrl = ''
    const fake = new FakeSocket()
    new ProgressSocket(
      { onMessage: () => undefined },
      {
        location: { protocol: 'https:', host: 'example.com' },
        socketFactory: (url) => {
          builtUrl = url
          return fake as unknown as WebSocket
        },
      },
    ).connect()
    expect(builtUrl).toBe('wss://example.com/api/ws/tasks_progress')
  })
})

describe('ProgressSocket — messages', () => {
  it('parses JSON frames and delivers them to onMessage', () => {
    const fake = new FakeSocket()
    const onMessage = vi.fn()
    const socket = new ProgressSocket(
      { onMessage },
      {
        location: { protocol: 'http:', host: 'x' },
        socketFactory: () => fake as unknown as WebSocket,
      },
    )
    socket.connect()

    fake.emit('message', {
      data: JSON.stringify({ '12': { sn: 12, rate: 5, status: '下載', filename: 'a.mp4' } }),
    })

    expect(onMessage).toHaveBeenCalledWith({
      '12': { sn: 12, rate: 5, status: '下載', filename: 'a.mp4' },
    })
  })

  it('ignores malformed JSON frames silently', () => {
    const fake = new FakeSocket()
    const onMessage = vi.fn()
    const socket = new ProgressSocket(
      { onMessage },
      {
        location: { protocol: 'http:', host: 'x' },
        socketFactory: () => fake as unknown as WebSocket,
      },
    )
    socket.connect()
    fake.emit('message', { data: 'not-json' })
    expect(onMessage).not.toHaveBeenCalled()
  })
})

describe('ProgressSocket — close()', () => {
  it('close() calls WebSocket.close', () => {
    const fake = new FakeSocket()
    const socket = new ProgressSocket(
      { onMessage: () => undefined },
      {
        location: { protocol: 'http:', host: 'x' },
        socketFactory: () => fake as unknown as WebSocket,
      },
    )
    socket.connect()
    socket.close()
    expect(fake.close).toHaveBeenCalledTimes(1)
  })
})

// ---------------------------------------------------------------------------
// State machine tests
// ---------------------------------------------------------------------------
describe('ProgressSocket — state transitions', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('starts in connecting state', () => {
    const { socket } = makeSocket()
    expect(socket.state.value).toBe('connecting')
    socket.connect()
    expect(socket.state.value).toBe('connecting')
  })

  it('transitions to open when the socket fires open', () => {
    const { socket, fake } = makeSocket()
    socket.connect()
    fake.emit('open', {})
    expect(socket.state.value).toBe('open')
  })

  it('transitions to closed on close event', () => {
    const { socket, fake } = makeSocket()
    socket.connect()
    fake.emit('open', {})
    fake.emit('close', new CloseEvent('close'))
    expect(socket.state.value).toBe('closed')
  })

  it('transitions back to connecting after backoff reconnect', () => {
    const fakeInstances: FakeSocket[] = []
    const socket = new ProgressSocket(
      { onMessage: vi.fn() },
      {
        location: { protocol: 'http:', host: 'x' },
        socketFactory: () => {
          const f = new FakeSocket()
          fakeInstances.push(f)
          return f as unknown as WebSocket
        },
        timerFactory: fakeTimerFactory(),
      },
    )
    socket.connect()

    const first = fakeInstances[0]
    first.emit('open', {})
    expect(socket.state.value).toBe('open')

    first.emit('close', new CloseEvent('close'))
    expect(socket.state.value).toBe('closed')

    // Advance past the first backoff delay (1000 ms).
    vi.advanceTimersByTime(1001)
    expect(socket.state.value).toBe('connecting')

    socket.close()
  })

  it('resets backoff index to 0 on successful reconnect', () => {
    const fakeInstances: FakeSocket[] = []
    const socket = new ProgressSocket(
      { onMessage: vi.fn() },
      {
        location: { protocol: 'http:', host: 'x' },
        socketFactory: () => {
          const f = new FakeSocket()
          fakeInstances.push(f)
          return f as unknown as WebSocket
        },
        timerFactory: fakeTimerFactory(),
      },
    )

    // First connect → open → close → reconnect → open → close again.
    // If backoff resets, the second disconnect should schedule 1000 ms again.
    socket.connect()
    fakeInstances[0].emit('open', {})
    fakeInstances[0].emit('close', new CloseEvent('close'))
    vi.advanceTimersByTime(1001) // reconnect fires

    fakeInstances[1].emit('open', {}) // backoff resets
    fakeInstances[1].emit('close', new CloseEvent('close'))

    // Should schedule 1000 ms again (not 2000).
    vi.advanceTimersByTime(1001)
    expect(fakeInstances.length).toBe(3) // third connect happened

    socket.close()
  })
})

// ---------------------------------------------------------------------------
// Backoff timing tests
// ---------------------------------------------------------------------------
describe('ProgressSocket — exponential backoff', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('reconnects with 1 / 2 / 4 / 8 / 16 / 30 / 30 s backoff', () => {
    const fakeInstances: FakeSocket[] = []
    let connectCallCount = 0

    const socket = new ProgressSocket(
      { onMessage: vi.fn() },
      {
        location: { protocol: 'http:', host: 'x' },
        socketFactory: () => {
          connectCallCount++
          const f = new FakeSocket()
          fakeInstances.push(f)
          return f as unknown as WebSocket
        },
        timerFactory: fakeTimerFactory(),
      },
    )

    socket.connect() // index 0 → first backoff 1000 ms
    expect(connectCallCount).toBe(1)

    const expected = [1000, 2000, 4000, 8000, 16000, 30000, 30000]

    for (let i = 0; i < expected.length; i++) {
      fakeInstances[i].emit('close', new CloseEvent('close'))

      // Advance 1 ms less than expected — no reconnect yet.
      vi.advanceTimersByTime(expected[i] - 1)
      expect(connectCallCount).toBe(i + 1) // no new connect

      // Advance the final ms — reconnect fires.
      vi.advanceTimersByTime(2) // +2 to cross the boundary
      expect(connectCallCount).toBe(i + 2) // new connect happened
    }

    socket.close()
  })
})

// ---------------------------------------------------------------------------
// user close() — no reconnect
// ---------------------------------------------------------------------------
describe('ProgressSocket — close() prevents reconnect', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('does not reconnect after user calls close()', () => {
    const fakeInstances: FakeSocket[] = []
    const socket = new ProgressSocket(
      { onMessage: vi.fn() },
      {
        location: { protocol: 'http:', host: 'x' },
        socketFactory: () => {
          const f = new FakeSocket()
          fakeInstances.push(f)
          return f as unknown as WebSocket
        },
        timerFactory: fakeTimerFactory(),
      },
    )

    socket.connect()
    expect(fakeInstances.length).toBe(1)

    socket.close() // user closes
    fakeInstances[0].emit('close', new CloseEvent('close'))

    vi.advanceTimersByTime(60_000) // wait way past any backoff
    expect(fakeInstances.length).toBe(1) // no new sockets created
  })
})

// ---------------------------------------------------------------------------
// connect() idempotency
// ---------------------------------------------------------------------------
describe('ProgressSocket — connect() idempotency', () => {
  it('test_connect_is_idempotent_when_already_open: calling connect() twice creates only one WebSocket', () => {
    const instances: FakeSocket[] = []
    const socket = new ProgressSocket(
      { onMessage: vi.fn() },
      {
        location: { protocol: 'http:', host: 'x' },
        socketFactory: () => {
          const f = new FakeSocket()
          instances.push(f)
          // Simulate an already-open socket: readyState === 1 (OPEN).
          Object.defineProperty(f, 'readyState', { get: () => 1 })
          return f as unknown as WebSocket
        },
      },
    )

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
// showDisconnectedBanner grace period (3 s)
// ---------------------------------------------------------------------------
describe('ProgressSocket — showDisconnectedBanner grace period', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  function makeSocketNoReconnect() {
    // We disable reconnect by closing user before the test starts — but we
    // actually want to test the banner grace period without reconnecting, so
    // we use a factory that just returns fresh stubs.
    const fakeInstances: FakeSocket[] = []
    const socket = new ProgressSocket(
      { onMessage: vi.fn() },
      {
        location: { protocol: 'http:', host: 'x' },
        socketFactory: () => {
          const f = new FakeSocket()
          fakeInstances.push(f)
          return f as unknown as WebSocket
        },
        timerFactory: fakeTimerFactory(),
      },
    )
    return { socket, fakeInstances }
  }

  it('banner is false immediately after close', () => {
    const { socket, fakeInstances } = makeSocketNoReconnect()
    socket.connect()
    fakeInstances[0].emit('open', {})
    fakeInstances[0].emit('close', new CloseEvent('close'))

    expect(socket.showDisconnectedBanner.value).toBe(false)
    socket.close()
  })

  it('banner is still false at 2.5 s after close', () => {
    const { socket, fakeInstances } = makeSocketNoReconnect()
    socket.connect()
    fakeInstances[0].emit('open', {})
    fakeInstances[0].emit('close', new CloseEvent('close'))

    vi.advanceTimersByTime(2500)
    expect(socket.showDisconnectedBanner.value).toBe(false)
    socket.close()
  })

  it('banner becomes true at 3.5 s after close', () => {
    const { socket, fakeInstances } = makeSocketNoReconnect()
    socket.connect()
    fakeInstances[0].emit('open', {})
    fakeInstances[0].emit('close', new CloseEvent('close'))

    // Also advance past the 1 s reconnect (the reconnect will find a new
    // fake but it won't open, so state stays closed).
    vi.advanceTimersByTime(3500)
    expect(socket.showDisconnectedBanner.value).toBe(true)
    socket.close()
  })

  it('banner stays false when reconnect succeeds before 3 s', () => {
    const fakeInstances: FakeSocket[] = []
    const socket = new ProgressSocket(
      { onMessage: vi.fn() },
      {
        location: { protocol: 'http:', host: 'x' },
        socketFactory: () => {
          const f = new FakeSocket()
          fakeInstances.push(f)
          return f as unknown as WebSocket
        },
        timerFactory: fakeTimerFactory(),
      },
    )

    socket.connect()
    fakeInstances[0].emit('open', {})
    fakeInstances[0].emit('close', new CloseEvent('close'))

    // Reconnect fires at 1 s; immediately open it.
    vi.advanceTimersByTime(1001)
    fakeInstances[1].emit('open', {})

    // Now advance past the original 3 s window.
    vi.advanceTimersByTime(2500)

    expect(socket.showDisconnectedBanner.value).toBe(false)
    socket.close()
  })
})

// ---------------------------------------------------------------------------
// lastTasks — last non-empty snapshot
// ---------------------------------------------------------------------------
describe('ProgressSocket — lastTasks', () => {
  it('stores the last non-empty tasks snapshot', () => {
    const fake = new FakeSocket()
    const received: unknown[] = []
    const socket = new ProgressSocket(
      { onMessage: (t) => received.push(t) },
      {
        location: { protocol: 'http:', host: 'x' },
        socketFactory: () => fake as unknown as WebSocket,
      },
    )
    socket.connect()

    fake.emit('message', {
      data: JSON.stringify({ '1': { sn: 1, rate: 50, status: '下載', filename: 'a.mp4' } }),
    })
    expect(socket.lastTasks.value).toEqual({
      '1': { sn: 1, rate: 50, status: '下載', filename: 'a.mp4' },
    })

    // Empty payload should NOT overwrite lastTasks.
    fake.emit('message', { data: JSON.stringify({}) })
    expect(socket.lastTasks.value).toEqual({
      '1': { sn: 1, rate: 50, status: '下載', filename: 'a.mp4' },
    })
  })
})
