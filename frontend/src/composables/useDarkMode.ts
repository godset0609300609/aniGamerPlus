import { onMounted, onUnmounted, ref, watchEffect } from 'vue'

export type ThemePreference = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'ag-theme'

function readStoredPreference(): ThemePreference {
  if (typeof localStorage === 'undefined') return 'system'
  const raw = localStorage.getItem(STORAGE_KEY)
  if (raw === 'light' || raw === 'dark' || raw === 'system') return raw
  return 'system'
}

function prefersDark(): boolean {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches === true
}

/**
 * Reactive dark-mode state + controls. Toggles the ``dark`` class on
 * <html> to flip Element Plus (and our own) CSS variables.
 */
export function useDarkMode() {
  const preference = ref<ThemePreference>(readStoredPreference())
  const isDark = ref(false)

  function resolve(pref: ThemePreference): boolean {
    return pref === 'dark' || (pref === 'system' && prefersDark())
  }

  function apply(): void {
    isDark.value = resolve(preference.value)
    const el = document.documentElement
    if (isDark.value) el.classList.add('dark')
    else el.classList.remove('dark')
  }

  function setPreference(next: ThemePreference): void {
    preference.value = next
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      /* storage may be unavailable; ignore */
    }
  }

  function toggle(): void {
    setPreference(isDark.value ? 'light' : 'dark')
  }

  watchEffect(apply)

  // Follow the OS theme when the user is on "system".
  const media = typeof window !== 'undefined' ? window.matchMedia?.('(prefers-color-scheme: dark)') : null
  const onSystemChange = (): void => {
    if (preference.value === 'system') apply()
  }
  onMounted(() => media?.addEventListener?.('change', onSystemChange))
  onUnmounted(() => media?.removeEventListener?.('change', onSystemChange))

  return { preference, isDark, setPreference, toggle }
}
