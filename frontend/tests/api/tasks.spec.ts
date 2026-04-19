import { describe, expect, it, vi } from 'vitest'
import { TasksApi, extractSn } from '@/api/tasks'
import type { ManualTaskRequest } from '@/types'

describe('extractSn', () => {
  it('returns the sn from a full 動畫瘋 URL', () => {
    expect(extractSn('https://ani.gamer.com.tw/animeVideo.php?sn=12345')).toBe('12345')
    expect(extractSn('ani.gamer.com.tw/animeVideo.php?sn=99')).toBe('99')
  })

  it('returns the input if it is already a sn', () => {
    expect(extractSn('12345')).toBe('12345')
    expect(extractSn('  98765 ')).toBe('98765')
  })

  it('returns empty string for non-numeric input', () => {
    expect(extractSn('')).toBe('')
    expect(extractSn('foo bar')).toBe('')
    expect(extractSn('https://google.com')).toBe('')
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
})
