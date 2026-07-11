/**
 * Format utilities for the monitor UI.
 */

import type { TaskProgressEntry } from '@/types'

/**
 * Clamps a progress rate — already on a 0-100 scale, NOT a 0-1 fraction —
 * into the valid 0-100 range for `<el-progress :percentage>`. Always use
 * this instead of `rate * 100`; see git d49c1ef ("fix(telegram): clamp
 * progress bar rate to fix 7387% rendering bug") for the production bug
 * this guards against.
 */
export function clampPercentage(rate: number): number {
  return Math.min(100, Math.max(0, Math.round(rate)))
}

/**
 * Derives 1-2 uppercase initials from a username for avatar display.
 * TaskProgressEntry has no avatar-URL field, so this is always used in
 * place of an image.
 */
export function ownerInitials(username: string): string {
  const trimmed = username.trim()
  if (!trimmed) return ''
  return trimmed.slice(0, 2).toUpperCase()
}

/**
 * Shared title formula for a task row/card: prefers
 * `《bangumi_name》 - EP episode`, falling back to the raw filename when
 * bangumi_name is absent. Used by both TaskCard.vue (kanban) and
 * MonitorTable.vue (table) so both surfaces agree on the same title.
 */
export function taskDisplayTitle(
  task: Pick<TaskProgressEntry, 'bangumi_name' | 'episode' | 'filename'>,
): string {
  if (task.bangumi_name) {
    const ep = task.episode ? ` - EP ${task.episode}` : ''
    return `《${task.bangumi_name}》${ep}`
  }
  return task.filename
}

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
 * Formats an ISO-8601 timestamp as a bare relative-time string (e.g. '9 分鐘前').
 * Returns empty string if iso is null/undefined/unparseable.
 *
 * Uses Intl.RelativeTimeFormat with 'zh-TW' locale.
 */
export function formatRelativeBare(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (isNaN(date.getTime())) return ''

  const diffMs = Date.now() - date.getTime()
  const diffSeconds = Math.floor(diffMs / 1000)
  const diffMinutes = Math.floor(diffSeconds / 60)
  const diffHours = Math.floor(diffMinutes / 60)
  const diffDays = Math.floor(diffHours / 24)

  const rtf = new Intl.RelativeTimeFormat('zh-TW', { numeric: 'always' })

  if (diffDays > 0) {
    return rtf.format(-diffDays, 'day')
  }
  if (diffHours > 0) {
    return rtf.format(-diffHours, 'hour')
  }
  if (diffMinutes > 0) {
    return rtf.format(-diffMinutes, 'minute')
  }
  return rtf.format(-diffSeconds, 'second')
}

/**
 * Formats an ISO-8601 timestamp as a relative "started N minutes ago" string.
 * Returns empty string if iso is null/undefined/unparseable.
 */
export function formatRelative(iso: string | null | undefined): string {
  const bare = formatRelativeBare(iso)
  if (!bare) return ''
  return `開始於 ${bare}`
}
