import { describe, expect, it, vi } from 'vitest'
import { ConfigApi, parseProxy, serializeProxy } from '@/api/config'
import type { WebSettings } from '@/types'

function makeSettings(overrides: Partial<WebSettings> = {}): WebSettings {
  return {
    bangumi_dir: '',
    temp_dir: '',
    classify_bangumi: true,
    lock_resolution: false,
    segment_download_mode: true,
    add_bangumi_name_to_video_filename: true,
    add_resolution_to_video_filename: true,
    download_resolution: '1080',
    default_download_mode: 'latest',
    check_frequency: 5,
    'multi-thread': 1,
    'bilibili-concurrent-parts': 2,
    multi_downloading_segment: 2,
    customized_video_filename_prefix: '',
    customized_video_filename_suffix: '',
    ua: '',
    use_mobile_api: false,
    danmu: false,
    use_proxy: false,
    proxy: '',
    read_sn_list_when_checking_update: true,
    read_config_when_checking_update: true,
    save_logs: true,
    quantity_of_logs: 7,
    download_cd: 60,
    parse_sn_cd: 5,
    parse_cd: 3,
    telegram: {
      enabled: false,
      public_url: '',
      bot_username: '',
      notify_on: ['completed', 'failed', 'cancelled'],
      admin_broadcast: true,
      rate_limit_per_minute: 30,
      health_alerts: true,
    },
    'bt-downloader': {
      enabled: false,
      'poll-interval-seconds': 300,
      'landing-poll-seconds': 60,
      'hanzi-convert': true,
      'landing-dir': '',
      'entry-retention-days': 90,
      'task-history-retention-days': 180,
      'auto-delete-remote-on-landed': true,
    },
    ...overrides,
  }
}

describe('ConfigApi', () => {
  it('load() GETs /config and returns parsed JSON', async () => {
    const expected = makeSettings({ 'multi-thread': 3 })
    const getJson = vi.fn().mockResolvedValue(expected)
    const api = new ConfigApi({ getJson } as never)

    const result = await api.load()
    expect(getJson).toHaveBeenCalledWith('/config')
    expect(result['multi-thread']).toBe(3)
  })

  it('save() PUTs /config with the settings', async () => {
    const putJson = vi.fn().mockResolvedValue({ status: 'ok' })
    const api = new ConfigApi({ putJson } as never)

    const body = makeSettings({ download_resolution: '720' })
    const result = await api.save(body)
    expect(putJson).toHaveBeenCalledWith('/config', body)
    expect(result.status).toBe('ok')
  })

  it('setCookie() PUTs /config/cookie with the cookie string', async () => {
    const putJson = vi.fn().mockResolvedValue({ status: 'ok' })
    const api = new ConfigApi({ putJson } as never)

    await api.setCookie('BAHAMUT_SESSID=abc123; other=val')
    expect(putJson).toHaveBeenCalledWith('/config/cookie', {
      cookie: 'BAHAMUT_SESSID=abc123; other=val',
    })
  })

  it('getCookieStatus() GETs /config/cookie/status', async () => {
    const getJson = vi.fn().mockResolvedValue({ configured: true })
    const api = new ConfigApi({ getJson } as never)

    const result = await api.getCookieStatus()
    expect(getJson).toHaveBeenCalledWith('/config/cookie/status')
    expect(result.configured).toBe(true)
  })

  it('setBilibiliCookie() PUTs /config/bilibili-cookie with the cookie string', async () => {
    const putJson = vi.fn().mockResolvedValue({ status: 'ok' })
    const api = new ConfigApi({ putJson } as never)

    const result = await api.setBilibiliCookie('SESSDATA=abc123; buvid3=xyz')
    expect(putJson).toHaveBeenCalledWith('/config/bilibili-cookie', {
      cookie: 'SESSDATA=abc123; buvid3=xyz',
    })
    expect(result.status).toBe('ok')
  })

  it('getBilibiliCookieStatus() GETs /config/bilibili-cookie/status', async () => {
    const getJson = vi.fn().mockResolvedValue({ configured: true })
    const api = new ConfigApi({ getJson } as never)

    const result = await api.getBilibiliCookieStatus()
    expect(getJson).toHaveBeenCalledWith('/config/bilibili-cookie/status')
    expect(result.configured).toBe(true)
  })

  it('setPutioToken() PUTs /config/putio-token with the token string', async () => {
    const putJson = vi.fn().mockResolvedValue({ status: 'ok' })
    const api = new ConfigApi({ putJson } as never)

    const result = await api.setPutioToken('putio-oauth-token-abc')
    expect(putJson).toHaveBeenCalledWith('/config/putio-token', {
      token: 'putio-oauth-token-abc',
    })
    expect(result.status).toBe('ok')
  })

  it('getPutioTokenStatus() GETs /config/putio-token/status', async () => {
    const getJson = vi.fn().mockResolvedValue({ configured: true })
    const api = new ConfigApi({ getJson } as never)

    const result = await api.getPutioTokenStatus()
    expect(getJson).toHaveBeenCalledWith('/config/putio-token/status')
    expect(result.configured).toBe(true)
  })

  it('setTelegramBotToken() PUTs /config/telegram-bot-token with the token string', async () => {
    const putJson = vi.fn().mockResolvedValue({ status: 'ok' })
    const api = new ConfigApi({ putJson } as never)

    const result = await api.setTelegramBotToken('123456:ABC-DEF')
    expect(putJson).toHaveBeenCalledWith('/config/telegram-bot-token', {
      bot_token: '123456:ABC-DEF',
    })
    expect(result.status).toBe('ok')
  })

  it('getTelegramBotTokenStatus() GETs /config/telegram-bot-token/status', async () => {
    const getJson = vi.fn().mockResolvedValue({ configured: true })
    const api = new ConfigApi({ getJson } as never)

    const result = await api.getTelegramBotTokenStatus()
    expect(getJson).toHaveBeenCalledWith('/config/telegram-bot-token/status')
    expect(result.configured).toBe(true)
  })

  it('setTelegramWebhookSecret() PUTs /config/telegram-webhook-secret with the secret string', async () => {
    const putJson = vi.fn().mockResolvedValue({ status: 'ok' })
    const api = new ConfigApi({ putJson } as never)

    const result = await api.setTelegramWebhookSecret('deadbeef')
    expect(putJson).toHaveBeenCalledWith('/config/telegram-webhook-secret', {
      webhook_secret: 'deadbeef',
    })
    expect(result.status).toBe('ok')
  })

  it('getTelegramWebhookSecretStatus() GETs /config/telegram-webhook-secret/status', async () => {
    const getJson = vi.fn().mockResolvedValue({ configured: true })
    const api = new ConfigApi({ getJson } as never)

    const result = await api.getTelegramWebhookSecretStatus()
    expect(getJson).toHaveBeenCalledWith('/config/telegram-webhook-secret/status')
    expect(result.configured).toBe(true)
  })
})

describe('parseProxy / serializeProxy', () => {
  it('parses bare ip:port', () => {
    expect(parseProxy('socks5://127.0.0.1:1080')).toEqual({
      protocol: 'SOCKS5',
      ip: '127.0.0.1',
      port: '1080',
      user: '',
      password: '',
    })
  })

  it('parses authenticated proxy', () => {
    expect(parseProxy('http://bob:s3cret@example.com:8080')).toEqual({
      protocol: 'HTTP',
      ip: 'example.com',
      port: '8080',
      user: 'bob',
      password: 's3cret',
    })
  })

  it('returns defaults for empty input', () => {
    expect(parseProxy('')).toEqual({
      protocol: 'HTTP',
      ip: '',
      port: '',
      user: '',
      password: '',
    })
  })

  it('round-trips proxy string', () => {
    const input = 'socks5://bob:s3cret@example.com:8080'
    const parts = parseProxy(input)
    expect(serializeProxy(parts)).toBe(input)
  })

  it('serializes without auth when credentials missing', () => {
    expect(
      serializeProxy({
        protocol: 'HTTP',
        ip: '127.0.0.1',
        port: '1080',
        user: '',
        password: '',
      }),
    ).toBe('http://127.0.0.1:1080')
  })

  it('returns empty string when ip and port are blank', () => {
    expect(
      serializeProxy({
        protocol: 'HTTP',
        ip: '',
        port: '',
        user: '',
        password: '',
      }),
    ).toBe('')
  })
})
