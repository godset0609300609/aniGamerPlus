import type { HttpClient } from './client'
import { http as defaultHttp } from './client'
import type { ManualTaskRequest, SimpleStatus, TaskHistoryEntry } from '@/types'

const VIDEO_URL_RE = /^(https:\/\/)?ani\.gamer\.com\.tw\/animeVideo\.php\?sn=/i

export class TasksApi {
  constructor(private readonly http: HttpClient = defaultHttp) {}

  submitManual(request: ManualTaskRequest): Promise<SimpleStatus> {
    return this.http.postJson<SimpleStatus>('/tasks/manual', request)
  }

  fetchHistory(days: number = 7): Promise<TaskHistoryEntry[]> {
    return this.http.getJson<TaskHistoryEntry[]>(`/tasks/history?days=${days}`)
  }
}

/**
 * Extracts the `sn` from either a raw sn number or a full 動畫瘋 episode URL.
 * Returns an empty string if the input cannot be interpreted as an sn.
 */
export function extractSn(input: string): string {
  const trimmed = input.trim()
  if (!trimmed) return ''
  const withoutPrefix = trimmed.replace(VIDEO_URL_RE, '')
  if (/^\d+$/.test(withoutPrefix)) return withoutPrefix
  return ''
}
