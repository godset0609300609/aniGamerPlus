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
  'bilibili-concurrent-parts': number
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
  telegram: TelegramSettings
  'bt-downloader': BtDownloaderSettings
}

export interface BtDownloaderSettings {
  enabled: boolean
  'poll-interval-seconds': number
  'landing-poll-seconds': number
  'hanzi-convert': boolean
  'landing-dir': string
  'entry-retention-days': number
  'task-history-retention-days': number
  'auto-delete-remote-on-landed': boolean
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
  source?: 'animad' | 'bilibili'
  bilingual?: boolean
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
  /** Owner's Discord avatar URL (admin view only); null when unavailable. */
  owner_avatar_url?: string | null
  source?: string | null
  external_id?: string | null
}

export type TaskProgressMap = Record<string, TaskProgressEntry>

/** Monitor page view toggle — kanban (default) or flat sortable/filterable table. */
export type MonitorViewMode = 'table' | 'kanban'

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
  source?: string | null
  external_id?: string | null
}

export interface SimpleStatus {
  status: string
}

export interface Health {
  status: string
  version: string | null
}

export type AnimeListMode = 'single' | 'latest' | 'all' | 'largest-sn'

export interface AnimeListEntry {
  sn: number
  enabled: boolean
  bilingual: boolean
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

/**
 * Client-facing Telegram settings — mirrors backend TelegramSettingsPublic.
 * bot_token / webhook_secret are intentionally excluded: they are write-only
 * via PUT /config/telegram-bot-token and PUT /config/telegram-webhook-secret
 * and must never round-trip through GET/PUT /config.
 */
export interface TelegramSettings {
  enabled: boolean
  public_url: string
  bot_username: string
  notify_on: string[]
  admin_broadcast: boolean
  rate_limit_per_minute: number
  health_alerts: boolean
}

export interface TelegramWebhookInfo {
  url?: string | null
  has_custom_certificate?: boolean
  pending_update_count?: number
  last_error_date?: number | null
  last_error_message?: string | null
  max_connections?: number | null
}

export interface BtFeed {
  id: number
  name: string
  url: string
  title_key: string
  link_key: string
  guid_key: string | null
  author_key: string | null
  enabled: boolean
  created_at: string
  updated_at: string
  entry_count: number
}

export interface BtFilter {
  id: number
  name: string
  keywords: string[]
  enabled: boolean
  sort_order: number
  created_at: string
  updated_at: string
}

export interface BtFeedEntry {
  id: number
  feed_id: number
  guid: string
  title: string
  link: string
  author: string | null
  published_at: string | null
  fetched_at: string
  matched_filter_id: number | null
  dispatched_at: string | null
  putio_transfer_id: number | null
  putio_status: string | null
  local_path: string | null
  remote_cleared_at: string | null
}

export interface BtEntriesPage {
  items: BtFeedEntry[]
  total: number
  page: number
  size: number
}

export interface BtProbeResult {
  available_keys: string[]
  sample_entries: Record<string, unknown>[]
}

export interface BtFilterMatchCount {
  count: number
  over_cap: boolean
}

export interface BtDispatchResponse {
  transfer_id: number
  status: string
}

// ---------------------------------------------------------------------------
// Telegram User API downloader (per-Discord-user MTProto session)
// ---------------------------------------------------------------------------

export type TgLoginStatus = 'pending' | 'awaiting_code' | 'awaiting_password' | 'success' | 'failed'
export type TgSessionStatusValue = 'no_session' | 'active' | 'revoked' | 'expired'

/**
 * Outcome of the most recent ``NotificationBinder.bind()`` attempt — mirrors
 * backend ``app.tg_downloader.notification_binder.NotificationBindResult``.
 */
export type TgNotificationBindStatus =
  | 'success'
  | 'bot_username_not_configured'
  | 'bot_username_invalid'
  | 'bot_not_found'
  | 'flood_wait'
  | 'telegram_error'
  | 'unknown_error'

export interface TgSession {
  status: TgSessionStatusValue
  phone_tail4: string | null
  telegram_user_id: number | null
  telegram_handle: string | null
  last_active_at: string | null
  notification_bound: boolean
  notification_bind_status: TgNotificationBindStatus | null
  notification_bind_error: string | null
}

/** Backwards-compatible alias — some call sites read this as "bind status". */
export type TgBindStatus = TgSession

export interface TgQrLoginResponse {
  login_token: string
  // B-10 (security audit): the raw tg://login?token=... deep link is the
  // login credential itself and no longer round-trips through the API —
  // only the rendered PNG (qr_code_png_base64) is exposed.
  qr_code_png_base64: string
}

/** Response body for POST /api/tg/session/rebind-notification. */
export interface TgRebindNotificationResponse {
  notification_bind_status: TgNotificationBindStatus
  notification_bind_error: string | null
}

export interface TgLoginStatusResponse {
  status: TgLoginStatus
  error?: string | null
  telegram_handle?: string | null
}

export interface TgPhoneLoginResponse {
  login_token: string
  phone: string
}

/** One of 'pending' / 'running' / 'done' / 'failed', or null if a backfill has never been requested. */
export type TgBackfillStatus = 'pending' | 'running' | 'done' | 'failed'

export interface TgWatchedChat {
  id: number
  chat_id: number
  chat_title: string
  media_types: string[]
  size_min_mb: number | null
  size_max_mb: number | null
  format_whitelist: string[] | null
  save_path: string | null
  enabled: boolean
  created_at: string
  backfill_enabled: boolean
  backfill_days: number
  backfill_status: TgBackfillStatus | null
  backfill_scanned_count: number
  backfill_matched_count: number
  backfill_started_at: string | null
  backfill_finished_at: string | null
  /** Highest Telegram message id the periodic catch-up scan has walked past, or null if never scanned. */
  last_scanned_message_id: number | null
  /** ISO-8601 UTC — when the last catch-up scan for this chat completed, or null if never scanned. */
  last_scanned_at: string | null
}

export interface TgAvailableChat {
  chat_id: number
  title: string
  type: string
  already_watched: boolean
}

// B-09/G-07 (security audit): GET /api/tg/chats/available now returns a
// capped, envelope-wrapped list rather than an unbounded bare array.
export interface TgAvailableChatsResponse {
  items: TgAvailableChat[]
  truncated: boolean
  total_seen: number
}

export interface TgDownloadedMedia {
  id: number
  chat_id: number
  chat_title: string | null
  message_id: number
  file_name: string
  file_size: number
  downloaded_at: string
  // Basename only (e.g. "episode01.mp4"), not the full server-side path —
  // the backend projects this down from the full path it stores internally
  // so the API never leaks the server's filesystem layout (HIGH-2 security fix).
  local_path: string
}

export interface TgDownloadsPage {
  items: TgDownloadedMedia[]
  total: number
  page: number
  size: number
}

// The actual download runs in the background — this only confirms the job
// was queued, not that the file has been replaced yet.
export interface TgRedownloadResponse {
  entry_id: number
  status: 'queued'
}
