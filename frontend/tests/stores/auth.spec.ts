import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Reset modules between tests so the module-level singletons (user, loading)
// are re-initialised for each test. We use dynamic imports after resetModules.
describe('useAuthStore', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    vi.resetModules()
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('initial loading state is true', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response('', { status: 401 }))
    const { useAuthStore } = await import('@/stores/auth')
    const { loading } = useAuthStore()
    expect(loading.value).toBe(true)
  })

  it('loadMe sets user on 200 response', async () => {
    const fakeUser = { id: '1', username: 'alice', avatar_url: null, role: 'admin' }
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(fakeUser), { status: 200 }),
    )

    const { useAuthStore } = await import('@/stores/auth')
    const { user, loading, loadMe } = useAuthStore()

    await loadMe()
    expect(loading.value).toBe(false)
    expect(user.value).toEqual(fakeUser)
  })

  it('loadMe sets user to null on 401', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response('', { status: 401 }))

    const { useAuthStore } = await import('@/stores/auth')
    const { user, loadMe } = useAuthStore()

    await loadMe()
    expect(user.value).toBeNull()
  })

  it('loadMe sets user to null on network error', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('Network error'))

    const { useAuthStore } = await import('@/stores/auth')
    const { user, loadMe } = useAuthStore()

    await loadMe()
    expect(user.value).toBeNull()
  })

  it('isAdmin returns true when role is admin', async () => {
    const fakeUser = { id: '2', username: 'bob', avatar_url: null, role: 'admin' }
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(fakeUser), { status: 200 }),
    )

    const { useAuthStore } = await import('@/stores/auth')
    const { isAdmin, loadMe } = useAuthStore()

    await loadMe()
    expect(isAdmin.value).toBe(true)
  })

  it('isAdmin returns false when role is downloader', async () => {
    const fakeUser = { id: '3', username: 'carol', avatar_url: null, role: 'downloader' }
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(fakeUser), { status: 200 }),
    )

    const { useAuthStore } = await import('@/stores/auth')
    const { isAdmin, loadMe } = useAuthStore()

    await loadMe()
    expect(isAdmin.value).toBe(false)
  })

  it('logout calls POST /api/auth/logout', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
    globalThis.fetch = fetchMock

    const { useAuthStore } = await import('@/stores/auth')
    const { user, logout } = useAuthStore()
    user.value = { id: '1', username: 'alice', avatar_url: null, role: 'admin' }

    await logout()

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/logout',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(user.value).toBeNull()
  })

  it('test_logout_clears_user_and_redirects_to_login: user is null and hash is #/login after logout', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))

    const { useAuthStore } = await import('@/stores/auth')
    const { user, logout } = useAuthStore()
    user.value = { id: '1', username: 'alice', avatar_url: null, role: 'admin' }

    await logout()

    expect(user.value).toBeNull()
    expect(window.location.hash).toBe('#/login')
  })

  it('logout clears user even when fetch rejects (best-effort)', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('Network failure'))

    const { useAuthStore } = await import('@/stores/auth')
    const { user, logout } = useAuthStore()
    user.value = { id: '1', username: 'alice', avatar_url: null, role: 'admin' }

    await logout()

    // Despite fetch failure, user must be cleared and redirect must happen.
    expect(user.value).toBeNull()
    expect(window.location.hash).toBe('#/login')
  })
})
