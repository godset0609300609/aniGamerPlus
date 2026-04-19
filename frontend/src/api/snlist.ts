import type { HttpClient } from './client'
import { http as defaultHttp } from './client'
import type { SimpleStatus } from '@/types'

export class SnListApi {
  constructor(private readonly http: HttpClient = defaultHttp) {}

  load(): Promise<string> {
    return this.http.getText('/sn_list')
  }

  save(content: string): Promise<SimpleStatus> {
    return this.http.putText<SimpleStatus>('/sn_list', content)
  }
}
