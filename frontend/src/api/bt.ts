import type { HttpClient } from './client'
import { http as defaultHttp } from './client'
import type {
  BtDispatchResponse,
  BtEntriesPage,
  BtFeed,
  BtFeedEntry,
  BtFilter,
  BtFilterMatchCount,
  BtProbeResult,
  SimpleStatus,
} from '@/types'

export type BtFeedCreate = Omit<BtFeed, 'id' | 'created_at' | 'updated_at' | 'entry_count'>

export class BtApi {
  constructor(private readonly http: HttpClient = defaultHttp) {}

  // ---------------------------------------------------------------------
  // Feeds
  // ---------------------------------------------------------------------

  listFeeds(): Promise<BtFeed[]> {
    return this.http.getJson<BtFeed[]>('/bt/feeds')
  }

  createFeed(body: BtFeedCreate): Promise<BtFeed> {
    return this.http.postJson<BtFeed>('/bt/feeds', body)
  }

  updateFeed(id: number, body: Partial<BtFeed>): Promise<BtFeed> {
    return this.http.patchJson<BtFeed>(`/bt/feeds/${id}`, body)
  }

  deleteFeed(id: number): Promise<SimpleStatus> {
    return this.http.deleteJson<SimpleStatus>(`/bt/feeds/${id}`)
  }

  probeFeed(url: string): Promise<BtProbeResult> {
    return this.http.postJson<BtProbeResult>('/bt/feeds/probe', { url })
  }

  // ---------------------------------------------------------------------
  // Filters
  // ---------------------------------------------------------------------

  listFilters(): Promise<BtFilter[]> {
    return this.http.getJson<BtFilter[]>('/bt/filters')
  }

  replaceFilters(filters: BtFilter[]): Promise<SimpleStatus> {
    return this.http.putJson<SimpleStatus>('/bt/filters', { filters })
  }

  // ---------------------------------------------------------------------
  // Entries
  // ---------------------------------------------------------------------

  listEntries(
    days: number = 7,
    filterId?: number,
    page: number = 1,
    size: number = 50,
    q?: string,
    putioStatus?: string | null,
  ): Promise<BtEntriesPage> {
    const params = [`days=${days}`, `page=${page}`, `size=${size}`]
    if (filterId) params.push(`filter_id=${filterId}`)
    if (q) params.push(`q=${encodeURIComponent(q)}`)
    if (putioStatus) params.push(`putio_status=${encodeURIComponent(putioStatus)}`)
    return this.http.getJson<BtEntriesPage>(`/bt/entries?${params.join('&')}`)
  }

  searchEntries(q: string, limit: number = 20): Promise<BtFeedEntry[]> {
    return this.http.getJson<BtFeedEntry[]>(
      `/bt/entries/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    )
  }

  filterMatchCount(keywords: string[]): Promise<BtFilterMatchCount> {
    return this.http.postJson<BtFilterMatchCount>('/bt/filters/match-count', { keywords })
  }

  dispatchEntry(entryId: number): Promise<BtDispatchResponse> {
    return this.http.postJson<BtDispatchResponse>(`/bt/entries/${entryId}/dispatch`, {})
  }
}
