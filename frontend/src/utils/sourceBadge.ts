/**
 * Shared source -> badge (color + label) mapping for the monitor UI.
 *
 * Both TaskCard.vue (kanban) and MonitorTable.vue (table) import this so
 * the two views always agree on exactly the same colors/labels instead of
 * duplicating the mapping in two places.
 */

export type SourceBadgeKey = 'animad' | 'bilibili' | 'bt' | 'tg' | 'other'

export interface SourceBadgeInfo {
  key: SourceBadgeKey
  label: string
  /** Exact hex (or CSS var for the unknown-source fallback) background color. */
  color: string
  /** Text color that stays readable against `color` in both light and dark theme. */
  textColor: string
}

const BAHAMUT_GREEN = '#3b8686'
const BILIBILI_BLUE = '#00a1d6'
// Matches Element Plus's --el-color-warning.
const BT_ORANGE = '#e6a23c'
// Telegram's own brand blue.
const TELEGRAM_BLUE = '#0088cc'
const UNKNOWN_BG = 'var(--el-fill-color-dark)'
const UNKNOWN_TEXT = 'var(--el-text-color-primary)'

/**
 * Maps a task's `source` field to a badge color + label.
 *
 * `null`/`undefined` are treated identically to `'animad'`: legacy /
 * non-BT-downloader progress payloads simply omit the field (see
 * `TaskProgressEntry` in `frontend/src/types.ts`) rather than encoding a
 * distinct "unknown" source. Any other, truly unrecognized string falls
 * back to a neutral gray badge labeled with the raw value.
 */
export function sourceBadgeInfo(source: string | null | undefined): SourceBadgeInfo {
  switch (source) {
    case 'bilibili':
      return { key: 'bilibili', label: 'Bilibili', color: BILIBILI_BLUE, textColor: '#fff' }
    case 'bt':
      return { key: 'bt', label: 'BT', color: BT_ORANGE, textColor: '#fff' }
    case 'tg':
      return { key: 'tg', label: 'Telegram', color: TELEGRAM_BLUE, textColor: '#fff' }
    case null:
    case undefined:
    case 'animad':
      return { key: 'animad', label: '動畫瘋', color: BAHAMUT_GREEN, textColor: '#fff' }
    default:
      return { key: 'other', label: source, color: UNKNOWN_BG, textColor: UNKNOWN_TEXT }
  }
}

export function sourceBadgeColor(source: string | null | undefined): string {
  return sourceBadgeInfo(source).color
}

export function sourceBadgeLabel(source: string | null | undefined): string {
  return sourceBadgeInfo(source).label
}

export function sourceBadgeTextColor(source: string | null | undefined): string {
  return sourceBadgeInfo(source).textColor
}
