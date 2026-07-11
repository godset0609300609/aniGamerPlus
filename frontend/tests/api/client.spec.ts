import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, HttpClient } from '@/api/client'

const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
  vi.restoreAllMocks()
})

function mockResponse(body: unknown, init?: ResponseInit): Response {
  const payload = typeof body === 'string' ? body : JSON.stringify(body)
  return new Response(payload, init)
}

describe('HttpClient', () => {
  it('prefixes paths with the baseUrl', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse({ ok: true }))
    globalThis.fetch = fetchMock as never

    const http = new HttpClient('/api')
    await http.getJson('/foo')
    expect(fetchMock).toHaveBeenCalledWith('/api/foo', { credentials: 'include' })
  })

  it('putJson sends JSON body with correct Content-Type', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse({ status: 'ok' }))
    globalThis.fetch = fetchMock as never

    const http = new HttpClient('/api')
    await http.putJson('/config', { a: 1 })

    const [, options] = fetchMock.mock.calls[0]!
    expect(options.method).toBe('PUT')
    expect(options.body).toBe(JSON.stringify({ a: 1 }))
    expect(options.headers).toEqual({ 'Content-Type': 'application/json' })
  })

  it('putText sends the text as-is', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse({ status: 'ok' }))
    globalThis.fetch = fetchMock as never

    const http = new HttpClient('/api')
    await http.putText('/sn_list', 'hello')
    const [, options] = fetchMock.mock.calls[0]!
    expect(options.body).toBe('hello')
    expect(options.headers).toEqual({ 'Content-Type': 'text/plain; charset=utf-8' })
  })

  it('patchJson sends JSON body with correct method and Content-Type', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse({ status: 'ok' }))
    globalThis.fetch = fetchMock as never

    const http = new HttpClient('/api')
    await http.patchJson('/bt/feeds/1', { enabled: false })

    const [url, options] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/bt/feeds/1')
    expect(options.method).toBe('PATCH')
    expect(options.body).toBe(JSON.stringify({ enabled: false }))
    expect(options.headers).toEqual({ 'Content-Type': 'application/json' })
  })

  it('deleteJson sends a DELETE request with credentials', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse({ status: 'ok' }))
    globalThis.fetch = fetchMock as never

    const http = new HttpClient('/api')
    const result = await http.deleteJson('/bt/feeds/1')

    expect(fetchMock).toHaveBeenCalledWith('/api/bt/feeds/1', {
      method: 'DELETE',
      credentials: 'include',
    })
    expect(result).toEqual({ status: 'ok' })
  })

  it('throws ApiError on non-2xx responses', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(mockResponse('boom', { status: 500 }))
    globalThis.fetch = fetchMock as never

    const http = new HttpClient('/api')
    await expect(http.getJson('/foo')).rejects.toBeInstanceOf(ApiError)
  })
})
