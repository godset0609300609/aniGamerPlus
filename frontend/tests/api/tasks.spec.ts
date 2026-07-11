import { describe, expect, it, vi } from 'vitest'
import { TasksApi, detectSource, extractSn } from '@/api/tasks'
import type { ManualTaskRequest } from '@/types'

describe('detectSource', () => {
  it('returns bilibili for full bilibili.com URL', () => {
    expect(detectSource('https://www.bilibili.com/video/BV1xx411c7mD')).toBe('bilibili')
    expect(detectSource('https://bilibili.com/video/BV1xx411c7mD')).toBe('bilibili')
  })

  it('returns bilibili for av number URL', () => {
    expect(detectSource('https://www.bilibili.com/video/av12345')).toBe('bilibili')
  })

  it('returns bilibili for b23.tv short link', () => {
    expect(detectSource('https://b23.tv/abc123')).toBe('bilibili')
  })

  it('returns bilibili for bare BV id', () => {
    expect(detectSource('BV1xx411c7mD')).toBe('bilibili')
  })

  it('returns bilibili for bare av id', () => {
    expect(detectSource('av12345')).toBe('bilibili')
  })

  it('returns animad for full 動畫瘋 URL', () => {
    expect(detectSource('https://ani.gamer.com.tw/animeVideo.php?sn=12345')).toBe('animad')
    expect(detectSource('ani.gamer.com.tw/animeVideo.php?sn=99')).toBe('animad')
  })

  it('returns animad for bare sn number', () => {
    expect(detectSource('12345')).toBe('animad')
    expect(detectSource('  98765 ')).toBe('animad')
  })

  it('returns null for empty input', () => {
    expect(detectSource('')).toBeNull()
    expect(detectSource('   ')).toBeNull()
  })

  it('returns bilibili for bilibili.com URL with trailing slash', () => {
    expect(detectSource('https://www.bilibili.com/video/BV1aBsaeeE8W/')).toBe('bilibili')
    expect(detectSource('https://www.bilibili.com/video/av12345/')).toBe('bilibili')
  })

  it('returns bilibili for bilibili.com URL with query string', () => {
    expect(detectSource('https://www.bilibili.com/video/BV1aBsaeeE8W?p=2')).toBe('bilibili')
  })

  it('returns bilibili for bilibili.com URL with trailing slash and query string', () => {
    expect(detectSource('https://www.bilibili.com/video/BV1aBsaeeE8W/?p=2&t=10')).toBe('bilibili')
  })

  it('returns bilibili for bilibili.com URL with fragment', () => {
    expect(detectSource('https://www.bilibili.com/video/BV1aBsaeeE8W#section')).toBe('bilibili')
  })

  it('returns null for garbage input', () => {
    expect(detectSource('foo bar')).toBeNull()
    expect(detectSource('https://google.com')).toBeNull()
    expect(detectSource('not-a-link')).toBeNull()
  })
})

describe('extractSn', () => {
  it('returns bilibili result for full bilibili.com BV URL', () => {
    const result = extractSn('https://www.bilibili.com/video/BV1xx411c7mD')
    expect(result).toEqual({ source: 'bilibili', bvid: 'https://www.bilibili.com/video/BV1xx411c7mD' })
  })

  it('returns bilibili result for b23.tv short link', () => {
    const result = extractSn('https://b23.tv/abc123')
    expect(result).toEqual({ source: 'bilibili', bvid: 'https://b23.tv/abc123' })
  })

  it('returns bilibili result for bare BV id', () => {
    const result = extractSn('BV1xx411c7mD')
    expect(result).toEqual({ source: 'bilibili', bvid: 'BV1xx411c7mD' })
  })

  it('returns bilibili result for bare av id', () => {
    const result = extractSn('av12345')
    expect(result).toEqual({ source: 'bilibili', bvid: 'av12345' })
  })

  it('returns animad result for full 動畫瘋 URL', () => {
    expect(extractSn('https://ani.gamer.com.tw/animeVideo.php?sn=12345')).toEqual({
      source: 'animad',
      sn: '12345',
    })
    expect(extractSn('ani.gamer.com.tw/animeVideo.php?sn=99')).toEqual({
      source: 'animad',
      sn: '99',
    })
  })

  it('returns animad result for bare sn number', () => {
    expect(extractSn('12345')).toEqual({ source: 'animad', sn: '12345' })
    expect(extractSn('  98765 ')).toEqual({ source: 'animad', sn: '98765' })
  })

  it('returns null for empty input', () => {
    expect(extractSn('')).toBeNull()
  })

  it('returns bilibili result for bilibili.com URL with trailing slash', () => {
    expect(extractSn('https://www.bilibili.com/video/BV1aBsaeeE8W/')).toEqual({
      source: 'bilibili',
      bvid: 'https://www.bilibili.com/video/BV1aBsaeeE8W/',
    })
    expect(extractSn('https://www.bilibili.com/video/av12345/')).toEqual({
      source: 'bilibili',
      bvid: 'https://www.bilibili.com/video/av12345/',
    })
  })

  it('returns bilibili result for bilibili.com URL with query string', () => {
    expect(extractSn('https://www.bilibili.com/video/BV1aBsaeeE8W?p=2')).toEqual({
      source: 'bilibili',
      bvid: 'https://www.bilibili.com/video/BV1aBsaeeE8W?p=2',
    })
  })

  it('returns bilibili result for bilibili.com URL with trailing slash and query string', () => {
    expect(extractSn('https://www.bilibili.com/video/BV1aBsaeeE8W/?p=2&t=10')).toEqual({
      source: 'bilibili',
      bvid: 'https://www.bilibili.com/video/BV1aBsaeeE8W/?p=2&t=10',
    })
  })

  it('returns bilibili result for bilibili.com URL with fragment', () => {
    expect(extractSn('https://www.bilibili.com/video/BV1aBsaeeE8W#section')).toEqual({
      source: 'bilibili',
      bvid: 'https://www.bilibili.com/video/BV1aBsaeeE8W#section',
    })
  })

  it('returns null for garbage input', () => {
    expect(extractSn('foo bar')).toBeNull()
    expect(extractSn('https://google.com')).toBeNull()
  })
})

describe('TasksApi', () => {
  it('POSTs to /tasks/manual with the request body', async () => {
    const postJson = vi.fn().mockResolvedValue({ status: 'ok' })
    const api = new TasksApi({ postJson } as never)
    const req: ManualTaskRequest = {
      sn: '12345',
      resolution: '720',
      mode: 'single',
      thread: 2,
      classify: true,
      danmu: false,
    }

    const res = await api.submitManual(req)
    expect(postJson).toHaveBeenCalledWith('/tasks/manual', req)
    expect(res.status).toBe('ok')
  })

  it('POSTs bilibili request with source field', async () => {
    const postJson = vi.fn().mockResolvedValue({ status: 'ok' })
    const api = new TasksApi({ postJson } as never)
    const req: ManualTaskRequest = {
      sn: 'BV1xx411c7mD',
      resolution: '1080',
      mode: 'single',
      thread: 1,
      classify: true,
      danmu: false,
      source: 'bilibili',
    }

    const res = await api.submitManual(req)
    expect(postJson).toHaveBeenCalledWith('/tasks/manual', req)
    expect(res.status).toBe('ok')
  })

  it('GETs /tasks/history with the given days parameter', async () => {
    const mockHistory = [
      {
        id: 1,
        sn: 42,
        filename: 'ep01.mp4',
        final_status: '下載完成',
        retries: 0,
        finished_at: '2026-04-18T10:00:00+00:00',
      },
    ]
    const getJson = vi.fn().mockResolvedValue(mockHistory)
    const api = new TasksApi({ getJson } as never)

    const result = await api.fetchHistory(7)
    expect(getJson).toHaveBeenCalledWith('/tasks/history?days=7')
    expect(result).toEqual(mockHistory)
  })

  it('fetchHistory defaults to 7 days', async () => {
    const getJson = vi.fn().mockResolvedValue([])
    const api = new TasksApi({ getJson } as never)

    await api.fetchHistory()
    expect(getJson).toHaveBeenCalledWith('/tasks/history?days=7')
  })

  it('dismissTask POSTs to the force-finish endpoint for the given sn', async () => {
    const postJson = vi.fn().mockResolvedValue({ status: 'ok' })
    const api = new TasksApi({ postJson } as never)

    const res = await api.dismissTask(12345)
    expect(postJson).toHaveBeenCalledWith('/monitor/progress/12345/force-finish', {})
    expect(res.status).toBe('ok')
  })
})
