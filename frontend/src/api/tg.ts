import type { HttpClient } from './client'
import { http as defaultHttp } from './client'
import type {
  TgAvailableChatsResponse,
  TgDownloadsPage,
  TgLoginStatusResponse,
  TgPhoneLoginResponse,
  TgQrLoginResponse,
  TgRebindNotificationResponse,
  TgSession,
  TgWatchedChat,
} from '@/types'
import type { SimpleStatus } from '@/types'

/** Server-managed backfill + catch-up-scan fields — never sent by the client, only read back from responses. */
type TgBackfillReadOnlyFields =
  | 'backfill_status'
  | 'backfill_scanned_count'
  | 'backfill_matched_count'
  | 'backfill_started_at'
  | 'backfill_finished_at'
  | 'last_scanned_message_id'
  | 'last_scanned_at'

export type TgWatchedChatCreate = Omit<TgWatchedChat, 'id' | 'created_at' | TgBackfillReadOnlyFields>
export type TgWatchedChatUpdate = Partial<Omit<TgWatchedChat, 'id' | 'chat_id' | 'created_at' | TgBackfillReadOnlyFields>>

export class TgApi {
  constructor(private readonly http: HttpClient = defaultHttp) {}

  // ---------------------------------------------------------------------
  // Session — QR login
  // ---------------------------------------------------------------------

  startQrLogin(): Promise<TgQrLoginResponse> {
    return this.http.postJson<TgQrLoginResponse>('/tg/session/qr-login', {})
  }

  pollQrLogin(loginToken: string): Promise<TgLoginStatusResponse> {
    return this.http.getJson<TgLoginStatusResponse>(`/tg/session/qr-login/${encodeURIComponent(loginToken)}`)
  }

  submitQrPassword(loginToken: string, password: string): Promise<TgLoginStatusResponse> {
    return this.http.postJson<TgLoginStatusResponse>(
      `/tg/session/qr-login/${encodeURIComponent(loginToken)}/password`,
      { password },
    )
  }

  // ---------------------------------------------------------------------
  // Session — phone login
  // ---------------------------------------------------------------------

  startPhoneLogin(phone: string): Promise<TgPhoneLoginResponse> {
    return this.http.postJson<TgPhoneLoginResponse>('/tg/session/phone-login', { phone })
  }

  submitPhoneCode(loginToken: string, code: string): Promise<TgLoginStatusResponse> {
    return this.http.postJson<TgLoginStatusResponse>(
      `/tg/session/phone-login/${encodeURIComponent(loginToken)}/code`,
      { code },
    )
  }

  submitPhonePassword(loginToken: string, password: string): Promise<TgLoginStatusResponse> {
    return this.http.postJson<TgLoginStatusResponse>(
      `/tg/session/phone-login/${encodeURIComponent(loginToken)}/password`,
      { password },
    )
  }

  // ---------------------------------------------------------------------
  // Session — status / unbind
  // ---------------------------------------------------------------------

  getSessionStatus(): Promise<TgSession> {
    return this.http.getJson<TgSession>('/tg/session')
  }

  deleteSession(): Promise<SimpleStatus> {
    return this.http.deleteJson<SimpleStatus>('/tg/session')
  }

  rebindNotification(): Promise<TgRebindNotificationResponse> {
    return this.http.postJson<TgRebindNotificationResponse>('/tg/session/rebind-notification', {})
  }

  // ---------------------------------------------------------------------
  // Watched chats
  // ---------------------------------------------------------------------

  listChats(): Promise<TgWatchedChat[]> {
    return this.http.getJson<TgWatchedChat[]>('/tg/chats')
  }

  createChat(body: TgWatchedChatCreate): Promise<TgWatchedChat> {
    return this.http.postJson<TgWatchedChat>('/tg/chats', body)
  }

  updateChat(id: number, body: TgWatchedChatUpdate): Promise<TgWatchedChat> {
    return this.http.patchJson<TgWatchedChat>(`/tg/chats/${id}`, body)
  }

  deleteChat(id: number): Promise<SimpleStatus> {
    return this.http.deleteJson<SimpleStatus>(`/tg/chats/${id}`)
  }

  retryBackfill(id: number): Promise<TgWatchedChat> {
    return this.http.postJson<TgWatchedChat>(`/tg/chats/${id}/backfill/retry`, {})
  }

  // B-09/G-07 (security audit): limit is capped server-side (default/max
  // 500/1000) — see TgAvailableChatsResponse.
  listAvailableChats(limit?: number): Promise<TgAvailableChatsResponse> {
    const query = limit != null ? `?limit=${limit}` : ''
    return this.http.getJson<TgAvailableChatsResponse>(`/tg/chats/available${query}`)
  }

  // ---------------------------------------------------------------------
  // Downloads
  // ---------------------------------------------------------------------

  listDownloads(page: number = 1, size: number = 50): Promise<TgDownloadsPage> {
    return this.http.getJson<TgDownloadsPage>(`/tg/downloads?page=${page}&size=${size}`)
  }
}
