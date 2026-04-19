/**
 * Auth store — a simple ref-based singleton that holds the current user.
 *
 * No Pinia required; the pattern matches the existing useDarkMode composable.
 *
 * Usage:
 *   const { user, loading, isAdmin, loadMe, logout } = useAuthStore()
 */

import { computed, ref } from 'vue'

export interface User {
  id: string
  username: string
  avatar_url: string | null
  role: 'admin' | 'downloader'
}

// Module-level singletons so every caller shares the same state.
const user = ref<User | null>(null)
const loading = ref<boolean>(true)

export function useAuthStore() {
  const isAdmin = computed(() => user.value?.role === 'admin')

  /** Fetch /api/auth/me.  On 401 sets user to null (not logged in). */
  async function loadMe(): Promise<void> {
    loading.value = true
    try {
      const res = await fetch('/api/auth/me', { credentials: 'include' })
      if (res.status === 401) {
        user.value = null
      } else if (res.ok) {
        user.value = (await res.json()) as User
      }
    } catch {
      // Network error — treat as unauthenticated.
      user.value = null
    } finally {
      loading.value = false
    }
  }

  /** POST /api/auth/logout and clear local state. */
  async function logout(): Promise<void> {
    try {
      await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
    } catch {
      // Best-effort; clear locally regardless.
    }
    user.value = null
    window.location.hash = '#/login'
  }

  return { user, loading, isAdmin, loadMe, logout }
}
