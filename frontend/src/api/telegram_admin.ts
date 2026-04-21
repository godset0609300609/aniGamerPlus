/**
 * Typed API wrappers for admin-only Telegram endpoints.
 *
 * All functions require the caller to be authenticated as admin; the server
 * enforces this via its own RBAC layer and will return 403 otherwise.
 */

import type { TelegramWebhookInfo } from '@/types'

/** POST /api/admin/telegram/webhook/register */
export async function registerWebhook(): Promise<{ ok: boolean; url: string }> {
  const res = await fetch('/api/admin/telegram/webhook/register', {
    method: 'POST',
    credentials: 'include',
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<{ ok: boolean; url: string }>
}

/** POST /api/admin/telegram/webhook/delete */
export async function deleteWebhook(): Promise<{ ok: boolean }> {
  const res = await fetch('/api/admin/telegram/webhook/delete', {
    method: 'POST',
    credentials: 'include',
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<{ ok: boolean }>
}

/** GET /api/admin/telegram/webhook/info */
export async function getWebhookInfo(): Promise<TelegramWebhookInfo> {
  const res = await fetch('/api/admin/telegram/webhook/info', {
    credentials: 'include',
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<TelegramWebhookInfo>
}

/** GET /api/admin/telegram/bot/me */
export async function getBotMe(): Promise<{ id: number; username?: string; first_name?: string }> {
  const res = await fetch('/api/admin/telegram/bot/me', {
    credentials: 'include',
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<{ id: number; username?: string; first_name?: string }>
}
