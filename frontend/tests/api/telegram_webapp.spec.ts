import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  getTelegramWebApp,
  isTelegramWebAppLaunch,
  loginViaTelegramWebApp,
  applyTelegramTheme,
} from '@/api/telegram_webapp'
import { http } from '@/api/client'

vi.mock('@/api/client', () => ({
  http: {
    postJson: vi.fn(),
  },
}))

describe('telegram_webapp', () => {
  beforeEach(() => {
    delete (window as Window & { Telegram?: unknown }).Telegram
    document.documentElement.classList.remove('dark')
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('isTelegramWebAppLaunch returns false without WebApp', () => {
    expect(isTelegramWebAppLaunch()).toBe(false)
  })

  it('isTelegramWebAppLaunch returns false with empty initData', () => {
    window.Telegram = { WebApp: { initData: '', ready: vi.fn(), expand: vi.fn() } }
    expect(isTelegramWebAppLaunch()).toBe(false)
  })

  it('isTelegramWebAppLaunch returns true with non-empty initData', () => {
    window.Telegram = { WebApp: { initData: 'auth_date=1&hash=x', ready: vi.fn(), expand: vi.fn() } }
    expect(isTelegramWebAppLaunch()).toBe(true)
  })

  it('getTelegramWebApp returns null without WebApp', () => {
    expect(getTelegramWebApp()).toBeNull()
  })

  it('getTelegramWebApp returns the WebApp object when present', () => {
    const wa = { initData: 'x', ready: vi.fn(), expand: vi.fn() }
    window.Telegram = { WebApp: wa }
    expect(getTelegramWebApp()).toBe(wa)
  })

  it('loginViaTelegramWebApp posts initData and returns the result', async () => {
    window.Telegram = { WebApp: { initData: 'foo=bar&hash=z', ready: vi.fn(), expand: vi.fn() } }
    vi.mocked(http.postJson).mockResolvedValueOnce({
      user_id: 'u-1', username: 'alice', role: 'downloader',
    })
    const out = await loginViaTelegramWebApp()
    expect(http.postJson).toHaveBeenCalledWith('/auth/telegram-webapp', { initData: 'foo=bar&hash=z' })
    expect(out.user_id).toBe('u-1')
    expect(out.username).toBe('alice')
    expect(out.role).toBe('downloader')
  })

  it('loginViaTelegramWebApp throws when WebApp unavailable', async () => {
    await expect(loginViaTelegramWebApp()).rejects.toThrow(/not available/)
  })

  it('applyTelegramTheme adds dark class for dark colorScheme', () => {
    window.Telegram = { WebApp: { initData: 'x', ready: vi.fn(), expand: vi.fn(), colorScheme: 'dark' } }
    applyTelegramTheme()
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('applyTelegramTheme does not add dark class for light colorScheme', () => {
    window.Telegram = { WebApp: { initData: 'x', ready: vi.fn(), expand: vi.fn(), colorScheme: 'light' } }
    applyTelegramTheme()
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })

  it('applyTelegramTheme does nothing without WebApp', () => {
    applyTelegramTheme()
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })
})
