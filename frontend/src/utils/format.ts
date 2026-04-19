/**
 * Format utilities for the monitor UI.
 */

/**
 * Formats a duration in seconds into a human-readable ETA string.
 * Returns empty string if seconds is null/undefined/negative.
 *
 * Examples:
 *   formatEta(45)    → '45s'
 *   formatEta(80)    → '1m 20s'
 *   formatEta(8100)  → '2h 15m'
 */
export function formatEta(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return ''
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

/**
 * Formats an ISO-8601 timestamp as a relative "started N minutes ago" string.
 * Returns empty string if iso is null/undefined/unparseable.
 *
 * Uses Intl.RelativeTimeFormat with 'zh-TW' locale.
 */
export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (isNaN(date.getTime())) return ''

  const diffMs = Date.now() - date.getTime()
  const diffSeconds = Math.floor(diffMs / 1000)
  const diffMinutes = Math.floor(diffSeconds / 60)
  const diffHours = Math.floor(diffMinutes / 60)

  const rtf = new Intl.RelativeTimeFormat('zh-TW', { numeric: 'always' })

  if (diffHours > 0) {
    return `開始於 ${rtf.format(-diffHours, 'hour')}`
  }
  if (diffMinutes > 0) {
    return `開始於 ${rtf.format(-diffMinutes, 'minute')}`
  }
  return `開始於 ${rtf.format(-diffSeconds, 'second')}`
}
