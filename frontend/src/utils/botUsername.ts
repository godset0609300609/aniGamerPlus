/**
 * Normalization + validation for the admin-configured Telegram notification
 * bot username (``settings.telegram.bot_username`` — SettingsView.vue's
 * "Telegram Bot 設定" section).
 *
 * A misconfigured bot_username is the leading cause of "帳號已綁定，通知綁定
 * 失敗" after a user completes the QR/phone User API bind (see
 * ``app.tg_downloader.notification_binder.NotificationBinder.bind`` on the
 * backend, which applies the same ``^@\w{4,32}$`` shape check). Normalizing
 * on blur — stripping a pasted ``https://t.me/`` prefix, trimming
 * whitespace, and auto-prepending ``@`` — heads off the most common typo
 * classes before they ever reach the backend.
 */

const TME_PREFIX_PATTERN = /^(?:https?:\/\/)?t\.me\//i
//: Telegram bot usernames: 4-32 chars, alphanumeric + underscore, after the
//: leading '@'. Mirrors the backend's NotificationBinder._BOT_USERNAME_RE.
export const BOT_USERNAME_PATTERN = /^@\w{4,32}$/

/**
 * Normalize a raw bot-username input into the canonical ``@handle`` form.
 *
 * - Trims surrounding whitespace.
 * - Strips a leading ``https://t.me/`` / ``http://t.me/`` / ``t.me/`` prefix
 *   (pasting a bot's share link is a common way to "type" a username).
 * - Prepends ``@`` if missing.
 *
 * Does not validate — an empty input normalizes to ``''`` (left alone, not
 * turned into a bare ``@``) so the field can still show "not configured".
 */
export function normalizeBotUsername(raw: string): string {
  const trimmed = raw.trim()
  if (!trimmed) return ''
  const withoutTmePrefix = trimmed.replace(TME_PREFIX_PATTERN, '')
  const stripped = withoutTmePrefix.trim()
  if (!stripped) return ''
  return stripped.startsWith('@') ? stripped : `@${stripped}`
}

/** True when *value* matches Telegram's bot-username shape (``@`` + 4-32 word chars). */
export function isValidBotUsername(value: string): boolean {
  return BOT_USERNAME_PATTERN.test(value)
}
