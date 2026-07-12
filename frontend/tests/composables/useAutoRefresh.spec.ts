/**
 * Unit tests for useAutoRefresh — polls a refetch function on a fixed
 * interval while the tab is visible, refetches on visibility regain, and
 * tears down its timer/listener on unmount.
 *
 * `onMounted`/`onUnmounted` only fire inside a real component lifecycle, so
 * each test mounts a tiny harness component that calls the composable in
 * its `setup()`, rather than invoking `useAutoRefresh` bare.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { useAutoRefresh } from '@/composables/useAutoRefresh'

function setVisibility(state: DocumentVisibilityState): void {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => state,
  })
}

function mountHarness(intervalMs: number, refetchFn: () => void | Promise<void>) {
  const Harness = defineComponent({
    setup() {
      useAutoRefresh(intervalMs, refetchFn)
      return () => null
    },
  })
  return mount(Harness)
}

beforeEach(() => {
  vi.useFakeTimers()
  setVisibility('visible')
})

afterEach(() => {
  vi.useRealTimers()
  setVisibility('visible')
})

describe('useAutoRefresh — polling', () => {
  it('test_use_auto_refresh_polls_at_interval_when_visible', async () => {
    const refetchFn = vi.fn()
    mountHarness(5000, refetchFn)

    expect(refetchFn).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(5000)
    expect(refetchFn).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(5000)
    expect(refetchFn).toHaveBeenCalledTimes(2)
  })
})

describe('useAutoRefresh — hidden tab', () => {
  it('test_use_auto_refresh_skips_when_hidden', async () => {
    const refetchFn = vi.fn()
    mountHarness(5000, refetchFn)

    setVisibility('hidden')
    await vi.advanceTimersByTimeAsync(15000)

    expect(refetchFn).not.toHaveBeenCalled()
  })

  it('refetches immediately when the tab regains visibility', async () => {
    const refetchFn = vi.fn()
    mountHarness(5000, refetchFn)

    setVisibility('hidden')
    document.dispatchEvent(new Event('visibilitychange'))
    expect(refetchFn).not.toHaveBeenCalled()

    setVisibility('visible')
    document.dispatchEvent(new Event('visibilitychange'))
    expect(refetchFn).toHaveBeenCalledTimes(1)
  })
})

describe('useAutoRefresh — teardown', () => {
  it('test_use_auto_refresh_cleans_up_on_unmount', async () => {
    const refetchFn = vi.fn()
    const wrapper = mountHarness(5000, refetchFn)

    wrapper.unmount()

    await vi.advanceTimersByTimeAsync(20000)
    expect(refetchFn).not.toHaveBeenCalled()

    // The visibilitychange listener must also be gone post-unmount.
    document.dispatchEvent(new Event('visibilitychange'))
    expect(refetchFn).not.toHaveBeenCalled()
  })
})
