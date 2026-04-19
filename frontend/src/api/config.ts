import type { HttpClient } from './client'
import { http as defaultHttp } from './client'
import type { ProxyParts, SimpleStatus, WebSettings } from '@/types'

export class ConfigApi {
  constructor(private readonly http: HttpClient = defaultHttp) {}

  load(): Promise<WebSettings> {
    return this.http.getJson<WebSettings>('/config')
  }

  save(settings: WebSettings): Promise<SimpleStatus> {
    return this.http.putJson<SimpleStatus>('/config', settings)
  }

  schemaKeys(): Promise<{ keys: string[] }> {
    return this.http.getJson('/config/schema')
  }

  /** Write the Bahamut cookie string (admin only). Never returns the old value. */
  async setCookie(cookie: string): Promise<void> {
    await this.http.putJson<SimpleStatus>('/config/cookie', { cookie })
  }

  /** Return whether a cookie is currently configured. Never returns the value. */
  getCookieStatus(): Promise<{ configured: boolean }> {
    return this.http.getJson<{ configured: boolean }>('/config/cookie/status')
  }
}

// ---------------------------------------------------------------------------
// Proxy string <-> parts helpers (used by the Settings view form).
// ---------------------------------------------------------------------------

export function parseProxy(proxy: string): ProxyParts {
  const parts: ProxyParts = {
    protocol: 'HTTP',
    ip: '',
    port: '',
    user: '',
    password: '',
  }
  if (!proxy) return parts

  const protoMatch = proxy.match(/^([^:]+):\/\//)
  parts.protocol = protoMatch ? protoMatch[1].toUpperCase() : 'HTTP'
  let remaining = proxy.replace(/^[^:]+:\/\//, '')

  if (remaining.includes('@')) {
    const [creds, rest] = remaining.split('@')
    const [user, password] = creds.split(':')
    parts.user = user ?? ''
    parts.password = password ?? ''
    remaining = rest
  }

  const [ip, port] = remaining.split(':')
  parts.ip = ip ?? ''
  parts.port = port ?? ''
  return parts
}

export function serializeProxy(parts: ProxyParts): string {
  if (!parts.ip && !parts.port) return ''
  const auth = parts.user && parts.password ? `${parts.user}:${parts.password}@` : ''
  return `${parts.protocol.toLowerCase()}://${auth}${parts.ip}:${parts.port}`
}
