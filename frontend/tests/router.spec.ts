/**
 * Unit tests for router.ts navigation guard.
 *
 * The navigation guard calls useAuthStore, so we stub the auth store module.
 * vi.resetModules() ensures each test gets a fresh router + guard.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

describe('router.ts — navigation guard', () => {
  beforeEach(() => {
    vi.resetModules()
  })

  it('allows navigation to public routes without auth (meta.public = true)', async () => {
    // Stub auth store — unauthenticated.
    vi.doMock('@/stores/auth', () => ({
      useAuthStore: () => ({ user: { value: null }, loading: { value: false } }),
    }))

    const { router } = await import('@/router')
    const result = router.resolve({ name: 'login' })
    expect(result.meta.public).toBe(true)

    // Navigate and confirm the guard passes.
    await router.push('/login')
    expect(router.currentRoute.value.path).toBe('/login')
  })

  it('redirects to /login when user is null and route is not public', async () => {
    vi.doMock('@/stores/auth', () => ({
      useAuthStore: () => ({ user: { value: null }, loading: { value: false } }),
    }))

    const { router } = await import('@/router')
    // Try to navigate to a protected route.
    await router.push('/monitor')

    // The guard should have redirected to login.
    expect(router.currentRoute.value.name).toBe('login')
  })

  it('allows navigation when user is authenticated', async () => {
    vi.doMock('@/stores/auth', () => ({
      useAuthStore: () => ({
        user: { value: { id: '1', username: 'alice', role: 'admin' } },
        loading: { value: false },
      }),
    }))

    const { router } = await import('@/router')
    await router.push('/monitor')

    expect(router.currentRoute.value.path).toBe('/monitor')
  })

  it('allows navigation when loading is still in progress', async () => {
    vi.doMock('@/stores/auth', () => ({
      useAuthStore: () => ({ user: { value: null }, loading: { value: true } }),
    }))

    const { router } = await import('@/router')
    await router.push('/monitor')

    // Guard returns true when loading, so monitor route is accessible.
    expect(router.currentRoute.value.path).toBe('/monitor')
  })
})
