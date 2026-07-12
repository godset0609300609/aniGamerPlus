/**
 * Put.io transfer status lifecycle helpers shared by the BT downloader
 * admin UI (EntriesTab's status column + status filter dropdown).
 */

export type BtStatusTagType = 'info' | 'warning' | 'primary' | 'success' | 'danger' | ''

export const BT_STATUS_LIFECYCLE = [
  'IN_QUEUE',
  'WAITING',
  'PREPARING_DOWNLOAD',
  'DOWNLOADING',
  'COMPLETING',
  'SEEDING',
  'COMPLETED',
  'ERROR',
] as const

// Post-landing remote-cleanup statuses — distinct from the raw Put.io
// transfer lifecycle above. Set directly by BtFeedEntryRepository
// (mark_remote_cleared / mark_remote_removed), not by Put.io itself.
export const REMOTE_CLEARED_STATUS = '遠端已清理'
export const REMOTE_REMOVED_STATUS = '遠端已移除'

// The full set of statuses the EntriesTab "Put.io 狀態" filter dropdown
// should offer. BT_STATUS_LIFECYCLE alone omits the two post-landing
// remote-cleanup statuses above (they aren't part of the raw Put.io
// transfer lifecycle), so without this a user could never filter the
// entries list down to just "遠端已清理" / "遠端已移除" rows. Appended at
// the end since both are terminal states that only ever follow COMPLETED.
export const BT_STATUS_FILTER_OPTIONS = [
  ...BT_STATUS_LIFECYCLE,
  REMOTE_CLEARED_STATUS,
  REMOTE_REMOVED_STATUS,
] as const

const TAG_TYPE_MAP: Record<string, BtStatusTagType> = {
  IN_QUEUE: 'info',
  WAITING: 'info',
  PREPARING_DOWNLOAD: 'warning',
  DOWNLOADING: 'warning',
  COMPLETING: 'warning',
  SEEDING: 'primary',
  COMPLETED: 'success',
  ERROR: 'danger',
  // 'info' renders as Element Plus's neutral gray-blue tag — a reasonable
  // stand-in for "soft-gray" without a custom CSS class.
  [REMOTE_REMOVED_STATUS]: 'info',
  // No built-in Element Plus tag type reads as teal; resolveTagClass below
  // adds a small custom-color class on top of the plain '' tag type.
  [REMOTE_CLEARED_STATUS]: '',
}

const LABEL_MAP: Record<string, string> = {
  IN_QUEUE: '排隊中',
  WAITING: '等待中',
  PREPARING_DOWNLOAD: '準備中',
  DOWNLOADING: '下載中',
  COMPLETING: '完成中',
  SEEDING: '做種中',
  COMPLETED: '已完成',
  ERROR: '失敗',
}

const TOOLTIP_MAP: Record<string, string> = {
  [REMOTE_CLEARED_STATUS]: '已在遠端刪除以節省空間，本地檔案仍在',
  [REMOTE_REMOVED_STATUS]: 'Put.io 端偵測到檔案不存在（可能被自動清理或使用者手動刪除）',
}

// CSS class hook for statuses that need a custom color beyond Element
// Plus's built-in tag palette — see the `.ag-tag-remote-cleared` rule in
// EntriesTab.vue.
const TAG_CLASS_MAP: Record<string, string> = {
  [REMOTE_CLEARED_STATUS]: 'ag-tag-remote-cleared',
}

export function resolveTagType(status: string | null): BtStatusTagType {
  if (status === null) return ''
  return TAG_TYPE_MAP[status] ?? ''
}

export function resolveLabel(status: string | null): string {
  if (status === null) return '未派送'
  return LABEL_MAP[status] ?? status
}

/** Extra CSS class for a status tag that needs a color outside Element Plus's built-in set. */
export function resolveTagClass(status: string | null): string {
  if (status === null) return ''
  return TAG_CLASS_MAP[status] ?? ''
}

/** Tooltip text explaining a status, or null when the status is self-explanatory. */
export function resolveTooltip(status: string | null): string | null {
  if (status === null) return null
  return TOOLTIP_MAP[status] ?? null
}
