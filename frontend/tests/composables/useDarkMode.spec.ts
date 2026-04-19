import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { useDarkMode } from '@/composables/useDarkMode'

function mountComposable<T>(fn: () => T): T {
  // useDarkMode registers onMounted/onUnmounted; calling it outside a real
  // component emits a warning but otherwise works for the logic we test here.
  return fn()
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
})
