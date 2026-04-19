import { describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { useBackendHealth } from '@/composables/useBackendHealth'

// ---------------------------------------------------------------------------
// Timer factory stub
// ---------------------------------------------------------------------------
function makeTimerFactory() {
  const callbacks: Map<number, () => void> = new Map()
  let nextId = 1

  return {
    setInterval: vi.fn((fn: () => void, _ms: number): number => {
      const id = nextId++
      callbacks.set(id, fn)
      return id
    }),
    clearInterval: vi.fn((id: number) => {
      callbacks.delete(id)
    }),
    tick: () => {
      callbacks.forEach((fn) => fn())
    },
  }
}

// ---------------------------------------------------------------------------
// fetch factory helpers
// ---------------------------------------------------------------------------
function makeFetch(response: { status: number; body?: object }) {
  return vi.fn(async () => ({
    ok: response.status >= 200 && response.status < 300,
    status: response.status,
    json: async () => response.body ?? {},
  })) as unknown as typeof fetch
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe('useBackendHealth — initial state', () => {
  it('starts as online', () => {
    const timer = makeTimerFactory()
    const fetchFn = makeFetch({ status: 200, body: { status: 'ok' } })
    const { state } = useBackendHealth({ fetchFn, timerFactory: timer })
    expect(state.value).toBe('online')
  })
})

describe('useBackendHealth — successful poll', () => {
  it('stays online when /api/health returns ok', async () => {
    const timer = makeTimerFactory()
    const fetchFn = makeFetch({ status: 200, body: { status: 'ok' } })
    const { state, start } = useBackendHealth({ fetchFn, timerFactory: timer })

    start()
    await nextTick()
    await nextTick()

    expect(state.value).toBe('online')
  })

  it('transitions to degraded immediately on degraded response', async () => {
    const timer = makeTimerFactory()
    const fetchFn = makeFetch({ status: 200, body: { status: 'degraded' } })
    const { state, start } = useBackendHealth({ fetchFn, timerFactory: timer })

    start()
    await nextTick()
    await nextTick()

    expect(state.value).toBe('degraded')
  })
})

describe('useBackendHealth — failure hysteresis (2 failures → offline)', () => {
  it('does not go offline after 1 failure', async () => {
    const timer = makeTimerFactory()
    let callCount = 0
    const fetchFn = vi.fn(async () => {
      callCount++
      throw new Error('network error')
    }) as unknown as typeof fetch

    const { state, start } = useBackendHealth({ fetchFn, timerFactory: timer })
    start()
    await nextTick()
    await nextTick()

    // First call has failed but threshold not reached.
    expect(state.value).toBe('online')
    expect(callCount).toBe(1)
  })

  it('goes offline after 2 consecutive failures', async () => {
    const timer = makeTimerFactory()
    const fetchFn = vi.fn(async () => {
      throw new Error('network error')
    }) as unknown as typeof fetch

    const { state, start } = useBackendHealth({ fetchFn, timerFactory: timer })
    start()
    await nextTick()
    await nextTick()

    // Trigger second failure via the interval callback.
    timer.tick()
    await nextTick()
    await nextTick()

    expect(state.value).toBe('offline')
  })

  it('recovers to online after 1 success following failures', async () => {
    const timer = makeTimerFactory()
    let shouldFail = true
    const fetchFn = vi.fn(async () => {
      if (shouldFail) throw new Error('down')
      return {
        ok: true,
        status: 200,
        json: async () => ({ status: 'ok' }),
      }
    }) as unknown as typeof fetch

    const { state, start } = useBackendHealth({ fetchFn, timerFactory: timer })
    start()
    await nextTick()
    await nextTick()
    // Second failure → offline
    timer.tick()
    await nextTick()
    await nextTick()
    expect(state.value).toBe('offline')

    // Now recover
    shouldFail = false
    timer.tick()
    await nextTick()
    await nextTick()
    expect(state.value).toBe('online')
  })
})

describe('useBackendHealth — retryCount', () => {
  it('increments retryCount on each failure', async () => {
    const timer = makeTimerFactory()
    const fetchFn = vi.fn(async () => {
      throw new Error('down')
    }) as unknown as typeof fetch

    const { retryCount, start } = useBackendHealth({ fetchFn, timerFactory: timer })
    start()
    await nextTick()
    await nextTick()
    expect(retryCount.value).toBe(1)

    timer.tick()
    await nextTick()
    await nextTick()
    expect(retryCount.value).toBe(2)
  })

  it('resets retryCount to 0 on success', async () => {
    const timer = makeTimerFactory()
    let shouldFail = true
    const fetchFn = vi.fn(async () => {
      if (shouldFail) throw new Error('down')
      return { ok: true, status: 200, json: async () => ({ status: 'ok' }) }
    }) as unknown as typeof fetch

    const { retryCount, start } = useBackendHealth({ fetchFn, timerFactory: timer })
    start()
    await nextTick()
    await nextTick()
    expect(retryCount.value).toBe(1)

    shouldFail = false
    timer.tick()
    await nextTick()
    await nextTick()
    expect(retryCount.value).toBe(0)
  })
})

describe('useBackendHealth — stop', () => {
  it('stop() calls clearInterval', () => {
    const timer = makeTimerFactory()
    const fetchFn = makeFetch({ status: 200, body: { status: 'ok' } })
    const { start, stop } = useBackendHealth({ fetchFn, timerFactory: timer })
    start()
    stop()
    expect(timer.clearInterval).toHaveBeenCalled()
  })

  it('start() is idempotent — does not register multiple intervals', () => {
    const timer = makeTimerFactory()
    const fetchFn = makeFetch({ status: 200, body: { status: 'ok' } })
    const { start } = useBackendHealth({ fetchFn, timerFactory: timer })
    start()
    start()
    expect(timer.setInterval).toHaveBeenCalledTimes(1)
  })
})

describe('useBackendHealth — ping', () => {
  it('ping() immediately checks health', async () => {
    const timer = makeTimerFactory()
    const fetchFn = makeFetch({ status: 200, body: { status: 'ok' } })
    const { ping, state } = useBackendHealth({ fetchFn, timerFactory: timer })

    await ping()
    expect(state.value).toBe('online')
    expect(fetchFn).toHaveBeenCalledTimes(1)
  })

  it('ping() increments retryCount on non-ok HTTP response (line 54-56)', async () => {
    const timer = makeTimerFactory()
    const fetchFn = makeFetch({ status: 503, body: {} })
    const { ping, retryCount } = useBackendHealth({ fetchFn, timerFactory: timer })

    await ping()
    expect(retryCount.value).toBe(1)
  })

  it('two non-ok HTTP responses transition state to offline', async () => {
    const timer = makeTimerFactory()
    const fetchFn = makeFetch({ status: 500, body: {} })
    const { state, start } = useBackendHealth({ fetchFn, timerFactory: timer })

    start()
    await nextTick()
    await nextTick()
    // second failure via interval tick
    timer.tick()
    await nextTick()
    await nextTick()

    expect(state.value).toBe('offline')
  })
})

describe('useBackendHealth — default timerFactory (window.setInterval)', () => {
  it('start() uses window.setInterval when no timerFactory is injected', () => {
    vi.useFakeTimers()
    const setIntervalSpy = vi.spyOn(window, 'setInterval')
    const fetchFn = makeFetch({ status: 200, body: { status: 'ok' } })

    const { start } = useBackendHealth({ fetchFn })
    start()

    expect(setIntervalSpy).toHaveBeenCalled()

    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('stop() uses window.clearInterval when no timerFactory is injected', () => {
    vi.useFakeTimers()
    const clearIntervalSpy = vi.spyOn(window, 'clearInterval')
    const fetchFn = makeFetch({ status: 200, body: { status: 'ok' } })

    const { start, stop } = useBackendHealth({ fetchFn })
    start()
    stop()

    expect(clearIntervalSpy).toHaveBeenCalled()

    vi.useRealTimers()
    vi.restoreAllMocks()
  })
})
