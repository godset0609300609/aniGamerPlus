import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { useDarkMode } from '@/composables/useDarkMode'

function mountComposable<T>(fn: () => T): T {
  // useDarkMode registers onMounted/onUnmounted; calling it outside a real
  // component emits a warning but otherwise works for the logic we test here.
  return fn()
}

/** Mount composable inside a real component so onMounted/onUnmounted fire. */
function mountInComponent() {
  let result!: ReturnType<typeof useDarkMode>
  const Wrapper = defineComponent({
    setup() {
      result = useDarkMode()
      return () => null
    },
  })
  mount(Wrapper)
  return result
}

describe('useDarkMode', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.classList.remove('dark')
    // Default to "light" system preference in happy-dom.
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('dark') ? false : true,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => true,
      onchange: null,
    }))
  })

  it('defaults to system preference (light)', async () => {
    const { isDark } = mountComposable(() => useDarkMode())
    await nextTick()
    expect(isDark.value).toBe(false)
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })

  it('toggles between light and dark and persists the choice', async () => {
    const { isDark, toggle } = mountComposable(() => useDarkMode())
    await nextTick()
    toggle()
    await nextTick()
    expect(isDark.value).toBe(true)
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(localStorage.getItem('ag-theme')).toBe('dark')

    toggle()
    await nextTick()
    expect(isDark.value).toBe(false)
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(localStorage.getItem('ag-theme')).toBe('light')
  })

  it('restores a stored preference on initialisation', async () => {
    localStorage.setItem('ag-theme', 'dark')
    const { isDark } = mountComposable(() => useDarkMode())
    await nextTick()
    expect(isDark.value).toBe(true)
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('setPreference("system") resolves against matchMedia', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('dark'),
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => true,
      onchange: null,
    }))
    const { isDark, setPreference } = mountComposable(() => useDarkMode())
    setPreference('system')
    await nextTick()
    expect(isDark.value).toBe(true)
  })

  it('setPreference does not throw when localStorage.setItem throws', async () => {
    // Simulate a storage-unavailable scenario (e.g., private browsing with full quota).
    vi.spyOn(Storage.prototype, 'setItem').mockImplementationOnce(() => {
      throw new DOMException('QuotaExceededError')
    })

    const { setPreference } = mountComposable(() => useDarkMode())
    // Should not throw despite localStorage failure.
    expect(() => setPreference('dark')).not.toThrow()

    vi.restoreAllMocks()
  })

  it('onSystemChange applies dark when OS switches to dark and preference is "system"', async () => {
    let capturedCallback: (() => void) | null = null
    vi.stubGlobal('matchMedia', (query: string) => ({
      // Start with light preference.
      matches: false,
      media: query,
      addEventListener: (_: string, cb: () => void) => {
        capturedCallback = cb
      },
      removeEventListener: () => undefined,
      dispatchEvent: () => true,
      onchange: null,
    }))

    // Use mountInComponent so onMounted fires and the event listener is registered.
    const { isDark } = mountInComponent()
    await nextTick()
    // Initially light (matches: false).
    expect(isDark.value).toBe(false)
    expect(capturedCallback).not.toBeNull()

    // Simulate OS change to dark — update matchMedia.matches, then fire callback.
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('dark'), // now returns true for dark
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => true,
      onchange: null,
    }))

    // Fire the registered system-change listener.
    capturedCallback!()
    await nextTick()

    expect(isDark.value).toBe(true)
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('onSystemChange does NOT apply when preference is "dark" (explicit override)', async () => {
    let capturedCallback: (() => void) | null = null
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: false,
      media: query,
      addEventListener: (_: string, cb: () => void) => {
        capturedCallback = cb
      },
      removeEventListener: () => undefined,
      dispatchEvent: () => true,
      onchange: null,
    }))

    const { isDark, setPreference } = mountInComponent()
    await nextTick()
    // Explicit dark override — onSystemChange should be a no-op.
    setPreference('dark')
    await nextTick()
    expect(isDark.value).toBe(true)

    // Simulate OS reporting light (matches: false) while pref is 'dark'.
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => true,
      onchange: null,
    }))

    // Fire system listener — should not change isDark because pref !== 'system'.
    expect(capturedCallback).not.toBeNull()
    capturedCallback!()
    await nextTick()

    // isDark must remain true (explicit 'dark' pref wins over system).
    expect(isDark.value).toBe(true)
  })
})
