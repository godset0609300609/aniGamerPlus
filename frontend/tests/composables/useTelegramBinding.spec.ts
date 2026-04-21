/**
 * Unit tests for useTelegramBinding composable.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useTelegramBinding } from '@/composables/useTelegramBinding'

// ---------------------------------------------------------------------------
// Timer / fetch helpers
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
    tick: (id?: number) => {
      if (id !== undefined) {
        callbacks.get(id)?.()
      } else {
        callbacks.forEach((fn) => fn())
      }
    },
    has: (id: number) => callbacks.has(id),
  }
}

function okFetch(body: object, status = 200): typeof fetch {
  return vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
    json: async () => body,
  })) as unknown as typeof fetch
}

function errFetch(status: number, detail: string): typeof fetch {
  return vi.fn(async () => ({
    ok: false,
    status,
    text: async () => JSON.stringify({ detail }),
    json: async () => ({ detail }),
  })) as unknown as typeof fetch
}

const mockOpenFn = vi.fn()
const baseNow = 1_700_000_000_000 // fixed epoch ms

beforeEach(() => {
  vi.clearAllMocks()
})

// ---------------------------------------------------------------------------
// loadStatus
// ---------------------------------------------------------------------------

describe('useTelegramBinding — loadStatus', () => {
  it('sets bound=false on fresh user', async () => {
    const timer = makeTimerFactory()
    const tb = useTelegramBinding({
      fetchFn: okFetch({ bound: false, chat_id: null, enabled: true, link_pending: false }) as typeof fetch,
      timerFactory: timer,
      openFn: mockOpenFn,
    })
    await tb.loadStatus()
    expect(tb.bound.value).toBe(false)
    expect(tb.notifyEnabled.value).toBe(true)
    expect(tb.linkPending.value).toBe(false)
  })

  it('sets bound=true when API says bound', async () => {
    const timer = makeTimerFactory()
    const tb = useTelegramBinding({
      fetchFn: okFetch({ bound: true, chat_id: 12345, enabled: true, link_pending: false }) as typeof fetch,
      timerFactory: timer,
      openFn: mockOpenFn,
    })
    await tb.loadStatus()
    expect(tb.bound.value).toBe(true)
  })

  it('sets error on fetch failure', async () => {
    const timer = makeTimerFactory()
    const tb = useTelegramBinding({
      fetchFn: errFetch(500, 'server error') as typeof fetch,
      timerFactory: timer,
      openFn: mockOpenFn,
    })
    await tb.loadStatus()
    expect(tb.error.value).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// startLink
// ---------------------------------------------------------------------------

describe('useTelegramBinding — startLink', () => {
  it('calls openFn with the link_url and transitions to pending', async () => {
    const timer = makeTimerFactory()
    const mockFetch = vi.fn(async (url: RequestInfo | URL) => {
      const path = typeof url === 'string' ? url : url.toString()
      if (path.includes('start-link')) {
        return {
          ok: true,
          status: 200,
          text: async () => JSON.stringify({ link_url: 'https://t.me/mybot?start=abc', expires_in_seconds: 600 }),
          json: async () => ({ link_url: 'https://t.me/mybot?start=abc', expires_in_seconds: 600 }),
        }
      }
      return {
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ bound: false, chat_id: null, enabled: true, link_pending: true }),
        json: async () => ({ bound: false, chat_id: null, enabled: true, link_pending: true }),
      }
    }) as unknown as typeof fetch

    const tb = useTelegramBinding({
      fetchFn: mockFetch,
      timerFactory: timer,
      openFn: mockOpenFn,
      nowFn: () => baseNow,
    })
    await tb.startLink()

    expect(mockOpenFn).toHaveBeenCalledWith('https://t.me/mybot?start=abc', '_blank', 'noopener,noreferrer')
    expect(tb.linkPending.value).toBe(true)
    // Countdown should start.
    expect(tb.secondsRemaining.value).toBeGreaterThan(0)
  })

  it('sets notConfigured when server returns telegram_not_configured', async () => {
    const timer = makeTimerFactory()
    const tb = useTelegramBinding({
      fetchFn: errFetch(400, 'telegram_not_configured') as typeof fetch,
      timerFactory: timer,
      openFn: mockOpenFn,
    })
    await tb.startLink()
    expect(tb.notConfigured.value).toBe(true)
    expect(mockOpenFn).not.toHaveBeenCalled()
  })

  it('sets error on unknown fetch failure', async () => {
    const timer = makeTimerFactory()
    const tb = useTelegramBinding({
      fetchFn: errFetch(500, 'something broke') as typeof fetch,
      timerFactory: timer,
      openFn: mockOpenFn,
    })
    await tb.startLink()
    expect(tb.error.value).not.toBeNull()
    expect(tb.notConfigured.value).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// unlink
// ---------------------------------------------------------------------------

describe('useTelegramBinding — unlink', () => {
  it('clears bound and linkPending after unlink', async () => {
    const timer = makeTimerFactory()
    const mockFetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ ok: true }),
      json: async () => ({ ok: true }),
    })) as unknown as typeof fetch

    const tb = useTelegramBinding({
      fetchFn: mockFetch,
      timerFactory: timer,
      openFn: mockOpenFn,
    })
    // Simulate already bound
    tb.bound.value = true
    tb.linkPending.value = true

    await tb.unlink()

    expect(tb.bound.value).toBe(false)
    expect(tb.linkPending.value).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// setNotifyEnabled
// ---------------------------------------------------------------------------

describe('useTelegramBinding — setNotifyEnabled', () => {
  it('calls PATCH and updates local state', async () => {
    const timer = makeTimerFactory()
    const capturedUrls: string[] = []
    const mockFetch = vi.fn(async (url: RequestInfo | URL) => {
      capturedUrls.push(typeof url === 'string' ? url : url.toString())
      return {
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ ok: true }),
        json: async () => ({ ok: true }),
      }
    }) as unknown as typeof fetch

    const tb = useTelegramBinding({
      fetchFn: mockFetch,
      timerFactory: timer,
      openFn: mockOpenFn,
    })
    tb.notifyEnabled.value = true
    await tb.setNotifyEnabled(false)

    expect(tb.notifyEnabled.value).toBe(false)
    expect(capturedUrls.some((u) => u.includes('notify-enabled'))).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

describe('useTelegramBinding — polling', () => {
  it('stops polling and sets bound when status becomes bound', async () => {
    const timer = makeTimerFactory()
    let statusCallCount = 0
    const mockFetch = vi.fn(async (url: RequestInfo | URL) => {
      const path = typeof url === 'string' ? url : url.toString()
      if (path.includes('start-link')) {
        return {
          ok: true,
          status: 200,
          text: async () => JSON.stringify({ link_url: 'https://t.me/bot?start=tok', expires_in_seconds: 600 }),
          json: async () => ({ link_url: 'https://t.me/bot?start=tok', expires_in_seconds: 600 }),
        }
      }
      statusCallCount++
      const shouldBeBound = statusCallCount >= 2
      return {
        ok: true,
        status: 200,
        text: async () =>
          JSON.stringify({ bound: shouldBeBound, chat_id: shouldBeBound ? 99 : null, enabled: true, link_pending: !shouldBeBound }),
        json: async () => ({
          bound: shouldBeBound,
          chat_id: shouldBeBound ? 99 : null,
          enabled: true,
          link_pending: !shouldBeBound,
        }),
      }
    }) as unknown as typeof fetch

    const tb = useTelegramBinding({
      fetchFn: mockFetch,
      timerFactory: timer,
      openFn: mockOpenFn,
      nowFn: () => baseNow,
    })

    await tb.startLink()
    expect(tb.linkPending.value).toBe(true)

    // Simulate first poll tick — still not bound.
    await timer.tick()
    // Wait for the async poll to complete.
    await new Promise((r) => setTimeout(r, 0))
    expect(tb.bound.value).toBe(false)

    // Simulate second poll tick — now bound.
    await timer.tick()
    await new Promise((r) => setTimeout(r, 0))
    expect(tb.bound.value).toBe(true)
    expect(tb.linkPending.value).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// countdownLabel
// ---------------------------------------------------------------------------

describe('useTelegramBinding — countdownLabel', () => {
  it('formats seconds correctly', async () => {
    const timer = makeTimerFactory()
    const mockFetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ link_url: 'https://t.me/bot?start=tok', expires_in_seconds: 585 }),
      json: async () => ({ link_url: 'https://t.me/bot?start=tok', expires_in_seconds: 585 }),
    })) as unknown as typeof fetch

    const tb = useTelegramBinding({
      fetchFn: mockFetch,
      timerFactory: timer,
      openFn: mockOpenFn,
      nowFn: () => baseNow,
    })
    await tb.startLink()
    // Immediately after start, should show ~9:45 (585 seconds).
    expect(tb.secondsRemaining.value).toBe(585)
    expect(tb.countdownLabel.value).toBe('9:45')
  })
})

// ---------------------------------------------------------------------------
// dispose
// ---------------------------------------------------------------------------

describe('useTelegramBinding — dispose', () => {
  it('clears all timers on dispose', async () => {
    const timer = makeTimerFactory()
    const mockFetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ link_url: 'https://t.me/bot?start=tok', expires_in_seconds: 600 }),
      json: async () => ({ link_url: 'https://t.me/bot?start=tok', expires_in_seconds: 600 }),
    })) as unknown as typeof fetch

    const tb = useTelegramBinding({
      fetchFn: mockFetch,
      timerFactory: timer,
      openFn: mockOpenFn,
      nowFn: () => baseNow,
    })
    await tb.startLink()
    // At this point we have at least a poll timer and a countdown timer.
    expect(timer.clearInterval).not.toHaveBeenCalled()

    tb.dispose()
    expect(timer.clearInterval).toHaveBeenCalled()
  })
})
