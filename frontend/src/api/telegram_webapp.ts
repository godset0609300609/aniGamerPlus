/**
 * Telegram Mini App auto-login helper.
 *
 * When the page is opened via the bot's "🌐 開啟網頁版" inline keyboard
 * button, ``window.Telegram.WebApp.initData`` is populated with a signed
 * payload.  We POST it to the backend which verifies the HMAC and issues
 * a session cookie — the rest of the app then behaves identically to a
 * Discord-OAuth-authenticated session.
 */
import { http } from './client'

interface TelegramWebApp {
  initData: string
  ready: () => void
  expand: () => void
  themeParams?: {
    bg_color?: string
    text_color?: string
    hint_color?: string
    link_color?: string
    button_color?: string
    button_text_color?: string
    secondary_bg_color?: string
  }
  colorScheme?: 'light' | 'dark'
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp }
  }
}

export function getTelegramWebApp(): TelegramWebApp | null {
  return window.Telegram?.WebApp ?? null
}

export function isTelegramWebAppLaunch(): boolean {
  const wa = getTelegramWebApp()
  return wa !== null && typeof wa.initData === 'string' && wa.initData.length > 0
}

export interface TelegramWebAppLoginResult {
  user_id: string
  username: string
  role: string
}

/**
 * Send the current initData to the backend and rely on the response to
 * carry the session cookie.  Throws if the backend returns non-2xx; the
 * caller should fall back to the normal login flow on failure.
 */
export async function loginViaTelegramWebApp(): Promise<TelegramWebAppLoginResult> {
  const wa = getTelegramWebApp()
  if (!wa || !wa.initData) {
    throw new Error('Telegram WebApp not available')
  }
  return http.postJson<TelegramWebAppLoginResult>('/auth/telegram-webapp', {
    initData: wa.initData,
  })
}

/**
 * Apply Telegram's themeParams to the document so Element Plus picks up
 * the correct dark/light scheme.  Best-effort — silently no-op if the
 * theme info is unavailable.
 */
export function applyTelegramTheme(): void {
  const wa = getTelegramWebApp()
  if (!wa) return
  if (wa.colorScheme === 'dark') {
    document.documentElement.classList.add('dark')
  }
  // Optional: map themeParams.bg_color etc. to CSS vars; skipped for now to
  // avoid clashing with the existing dark-vars stylesheet.
}
