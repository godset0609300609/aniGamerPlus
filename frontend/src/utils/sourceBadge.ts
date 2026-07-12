/**
 * Shared source -> badge (color + label) mapping for the monitor UI.
 *
 * Both TaskCard.vue (kanban) and MonitorTable.vue (table) import this so
 * the two views always agree on exactly the same colors/labels instead of
 * duplicating the mapping in two places.
 */

export type SourceBadgeKey = 'animad' | 'bilibili' | 'bt' | 'tg' | 'unknown' | 'other'

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
 * Every live download path (動畫瘋/animad, Bilibili, BT, Telegram) now sets
 * an explicit `source` on the progress entry it creates (see
 * `ProgressBus.start()` / `force_finish()` call sites in the backend), so
 * `null`/`undefined` no longer means "this is an animad entry that forgot to
 * tag itself" — that assumption predates the backend setting `source`
 * explicitly everywhere. A `null`/`undefined` source now genuinely means
 * either a legacy Redis entry written before this field existed, or a bug,
 * and is rendered as a neutral gray "unknown" badge rather than impersonating
 * 動畫瘋. Any other, truly unrecognized non-null string still falls back to a
 * neutral gray badge labeled with the raw value.
 */
export function sourceBadgeInfo(source: string | null | undefined): SourceBadgeInfo {
  switch (source) {
    case 'bilibili':
      return { key: 'bilibili', label: 'Bilibili', color: BILIBILI_BLUE, textColor: '#fff' }
    case 'bt':
      return { key: 'bt', label: 'BT', color: BT_ORANGE, textColor: '#fff' }
    case 'tg':
      return { key: 'tg', label: 'Telegram', color: TELEGRAM_BLUE, textColor: '#fff' }
    case 'animad':
      return { key: 'animad', label: '動畫瘋', color: BAHAMUT_GREEN, textColor: '#fff' }
    case null:
    case undefined:
      return { key: 'unknown', label: '未知', color: UNKNOWN_BG, textColor: UNKNOWN_TEXT }
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
