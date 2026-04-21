// Mirrors the pydantic models in backend/app/models.py.
// Keep this file in sync when the backend schema changes.

export type Resolution = '360' | '480' | '540' | '720' | '1080'
export type DefaultDownloadMode = 'all' | 'latest' | 'largest-sn'
export type ManualDownloadMode = 'single' | 'latest' | 'all' | 'largest-sn'

export interface WebSettings {
  bangumi_dir: string
  temp_dir: string
  classify_bangumi: boolean
  lock_resolution: boolean
  segment_download_mode: boolean
  add_bangumi_name_to_video_filename: boolean
  add_resolution_to_video_filename: boolean
  download_resolution: Resolution
  default_download_mode: DefaultDownloadMode
  check_frequency: number
  'multi-thread': number
  multi_downloading_segment: number
  customized_video_filename_prefix: string
  customized_video_filename_suffix: string
  ua: string
  use_mobile_api: boolean
  danmu: boolean
  use_proxy: boolean
  proxy: string
  read_sn_list_when_checking_update: boolean
  read_config_when_checking_update: boolean
  save_logs: boolean
  quantity_of_logs: number
  download_cd: number
  parse_sn_cd: number
  parse_cd: number
}

export interface ProxyParts {
  protocol: string
  ip: string
  port: string
  user: string
  password: string
}

export interface ManualTaskRequest {
  sn: string
  resolution: Resolution
  mode: ManualDownloadMode
  thread: number
  classify: boolean
  danmu: boolean
}

export interface TaskProgressEntry {
  sn: number
  rate: number
  status: string
  filename: string
  bangumi_name?: string | null
  episode?: string | null
  resolution?: string | null
  speed_mbps?: number | null
  eta_seconds?: number | null
  retries?: number
  started_at?: string | null
  finished_at?: string | null
  /** ISO-8601 UTC deadline. When set and in the future, the card shows a live "冷卻 Ns" countdown. */
  cooldown_until?: string | null
  owner_id?: string | null
  owner_username?: string | null
}

export type TaskProgressMap = Record<string, TaskProgressEntry>

/**
 * One row returned by GET /api/tasks/history.
 * Mirrors TaskHistoryEntryOut in backend/app/models.py.
 */
export interface TaskHistoryEntry {
  id: number
  sn: number
  filename: string
  bangumi_name?: string | null
  episode?: string | null
  resolution?: string | null
  final_status: string
  retries: number
  started_at?: string | null
  finished_at: string   // always present for completed/interrupted rows
  owner_id?: string | null
}

export interface SimpleStatus {
  status: string
}

export interface Health {
  status: string
  version: string | null
  working_dir: string | null
}

export type AnimeListMode = 'single' | 'latest' | 'all' | 'largest-sn'

export interface AnimeListEntry {
  sn: number
  enabled: boolean
  mode: AnimeListMode | null
  tag: string
  season: number
  custom_name: string | null
  comment: string
  anime_name: string | null
  downloaded_episodes: number
  known_episodes: number
  owner_id?: string | null
  owner_username?: string | null
  /** Set when this entry is auto-disabled due to a duplicate anime_name. */
  duplicate_of_entry_id?: number | null
  /** Resolved: the anime_name of the original entry this one duplicates. */
  duplicate_of_bangumi_name?: string | null
  /** Resolved: the owner username of the original entry this one duplicates. */
  duplicate_of_owner_username?: string | null
}

export interface AnimeListPayload {
  entries: AnimeListEntry[]
}
