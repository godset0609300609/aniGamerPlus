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
      bot_token: '',
      webhook_secret: '',
      public_url: '',
      notify_on: ['completed', 'failed', 'cancelled'],
      admin_broadcast: true,
      rate_limit_per_minute: 30,
      allow_localhost: false,
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
