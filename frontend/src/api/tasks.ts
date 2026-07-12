import type { HttpClient } from './client'
import { http as defaultHttp } from './client'
import type { ManualTaskRequest, SimpleStatus, TaskHistoryEntry } from '@/types'

const VIDEO_URL_RE = /^(https:\/\/)?ani\.gamer\.com\.tw\/animeVideo\.php\?sn=/i

export const BILIBILI_URL_RE =
  /^(?:https?:\/\/(?:www\.)?bilibili\.com\/video\/(?:BV\w+|av\d+)(?:\/)?(?:\?[^\s]*)?(?:#[^\s]*)?|https?:\/\/b23\.tv\/\S+|BV\w+|av\d+)$/i

export class TasksApi {
  constructor(private readonly http: HttpClient = defaultHttp) {}

  submitManual(request: ManualTaskRequest): Promise<SimpleStatus> {
    return this.http.postJson<SimpleStatus>('/tasks/manual', request)
  }

  fetchHistory(days: number = 7): Promise<TaskHistoryEntry[]> {
    return this.http.getJson<TaskHistoryEntry[]>(`/tasks/history?days=${days}`)
  }

  /**
   * Force-finishes a live-progress entry so it drops off MonitorView on the
   * next WS snapshot. Used by the card X button — unlike `cancelTask`
   * (`DELETE /api/tasks/{sn}`, which signals a *live* actor to stop), this
   * works even for a "ghost" card whose owning process is already dead.
   */
  dismissTask(sn: number): Promise<SimpleStatus> {
    return this.http.postJson<SimpleStatus>(`/monitor/progress/${sn}/force-finish`, {})
  }
}

export function detectSource(input: string): 'animad' | 'bilibili' | null {
  const trimmed = input.trim()
  if (!trimmed) return null
  if (BILIBILI_URL_RE.test(trimmed)) return 'bilibili'
  const withoutPrefix = trimmed.replace(VIDEO_URL_RE, '')
  if (/^\d+$/.test(withoutPrefix)) return 'animad'
  return null
}

export type ExtractSnResult =
  | { source: 'animad'; sn: string }
  | { source: 'bilibili'; bvid: string }
  | null

export function extractSn(input: string): ExtractSnResult {
  const trimmed = input.trim()
  if (!trimmed) return null
  if (BILIBILI_URL_RE.test(trimmed)) return { source: 'bilibili', bvid: trimmed }
  const withoutPrefix = trimmed.replace(VIDEO_URL_RE, '')
  if (/^\d+$/.test(withoutPrefix)) return { source: 'animad', sn: withoutPrefix }
  return null
}
