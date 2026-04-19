import type { HttpClient } from './client'
import { http as defaultHttp } from './client'
import type { AnimeListEntry, AnimeListPayload, SimpleStatus } from '@/types'

export class AnimeListApi {
  constructor(private readonly http: HttpClient = defaultHttp) {}

  list(): Promise<AnimeListPayload> {
    return this.http.getJson<AnimeListPayload>('/anime-list')
  }

  replaceAll(entries: AnimeListEntry[]): Promise<SimpleStatus> {
    return this.http.putJson<SimpleStatus>('/anime-list', { entries })
  }
}
