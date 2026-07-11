import { describe, expect, it, vi } from 'vitest'
import { BtApi } from '@/api/bt'
import type { BtEntriesPage, BtFeed, BtFeedEntry, BtFilter, BtProbeResult } from '@/types'

function makeFeed(overrides: Partial<BtFeed> = {}): BtFeed {
  return {
    id: 1,
    name: 'dmhy 動畫',
    url: 'https://share.dmhy.org/topics/rss/sort_id/2/rss.xml',
    title_key: 'title',
    link_key: 'link',
    guid_key: 'guid',
    author_key: null,
    enabled: true,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    entry_count: 0,
    ...overrides,
  }
}

function makeFilter(overrides: Partial<BtFilter> = {}): BtFilter {
  return {
    id: 1,
    name: 'LoliHouse 1080',
    keywords: ['LoliHouse', '1080'],
    enabled: true,
    sort_order: 0,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    ...overrides,
  }
}

function makeEntry(overrides: Partial<BtFeedEntry> = {}): BtFeedEntry {
  return {
    id: 1,
    feed_id: 1,
    guid: 'guid-1',
    title: '[LoliHouse] Example - 01 [1080p]',
    link: 'magnet:?xt=urn:btih:example',
    author: null,
    published_at: '2026-01-01T00:00:00+00:00',
    fetched_at: '2026-01-01T00:05:00+00:00',
    matched_filter_id: 1,
    dispatched_at: '2026-01-01T00:05:01+00:00',
    putio_transfer_id: 42,
    putio_status: 'IN_QUEUE',
    local_path: null,
    remote_cleared_at: null,
    ...overrides,
  }
}

describe('BtApi — feeds', () => {
  it('listFeeds() GETs /bt/feeds', async () => {
    const expected = [makeFeed()]
    const getJson = vi.fn().mockResolvedValue(expected)
    const api = new BtApi({ getJson } as never)

    const result = await api.listFeeds()
    expect(getJson).toHaveBeenCalledWith('/bt/feeds')
    expect(result).toEqual(expected)
  })

  it('createFeed() POSTs /bt/feeds with the body and returns the created feed', async () => {
    const created = makeFeed({ id: 5 })
    const postJson = vi.fn().mockResolvedValue(created)
    const api = new BtApi({ postJson } as never)

    const body = {
      name: 'dmhy 動畫',
      url: 'https://share.dmhy.org/topics/rss/sort_id/2/rss.xml',
      title_key: 'title',
      link_key: 'link',
      guid_key: 'guid',
      author_key: null,
      enabled: true,
    }
    const result = await api.createFeed(body)
    expect(postJson).toHaveBeenCalledWith('/bt/feeds', body)
    expect(result).toEqual(created)
  })

  it('updateFeed() PATCHes /bt/feeds/{id} with the partial body', async () => {
    const updated = makeFeed({ id: 5, enabled: false })
    const patchJson = vi.fn().mockResolvedValue(updated)
    const api = new BtApi({ patchJson } as never)

    const result = await api.updateFeed(5, { enabled: false })
    expect(patchJson).toHaveBeenCalledWith('/bt/feeds/5', { enabled: false })
    expect(result).toEqual(updated)
  })

  it('deleteFeed() DELETEs /bt/feeds/{id}', async () => {
    const deleteJson = vi.fn().mockResolvedValue({ status: 'ok' })
    const api = new BtApi({ deleteJson } as never)

    const result = await api.deleteFeed(5)
    expect(deleteJson).toHaveBeenCalledWith('/bt/feeds/5')
    expect(result.status).toBe('ok')
  })

  it('probeFeed() POSTs /bt/feeds/probe with the url and returns available keys + samples', async () => {
    const expected: BtProbeResult = {
      available_keys: ['title', 'link', 'guid', 'enclosure.url'],
      sample_entries: [{ title: 'Example 01', link: 'magnet:?xt=urn:btih:abc' }],
    }
    const postJson = vi.fn().mockResolvedValue(expected)
    const api = new BtApi({ postJson } as never)

    const result = await api.probeFeed('https://share.dmhy.org/topics/rss/sort_id/2/rss.xml')
    expect(postJson).toHaveBeenCalledWith('/bt/feeds/probe', {
      url: 'https://share.dmhy.org/topics/rss/sort_id/2/rss.xml',
    })
    expect(result).toEqual(expected)
  })
})

describe('BtApi — filters', () => {
  it('listFilters() GETs /bt/filters', async () => {
    const expected = [makeFilter()]
    const getJson = vi.fn().mockResolvedValue(expected)
    const api = new BtApi({ getJson } as never)

    const result = await api.listFilters()
    expect(getJson).toHaveBeenCalledWith('/bt/filters')
    expect(result).toEqual(expected)
  })

  it('replaceFilters() PUTs /bt/filters wrapping filters in { filters }', async () => {
    const putJson = vi.fn().mockResolvedValue({ status: 'ok' })
    const api = new BtApi({ putJson } as never)

    const filters = [makeFilter({ id: 1 }), makeFilter({ id: 2, name: 'other' })]
    const result = await api.replaceFilters(filters)
    expect(putJson).toHaveBeenCalledWith('/bt/filters', { filters })
    expect(result.status).toBe('ok')
  })

  it('replaceFilters() supports sending an empty list', async () => {
    const putJson = vi.fn().mockResolvedValue({ status: 'ok' })
    const api = new BtApi({ putJson } as never)

    await api.replaceFilters([])
    expect(putJson).toHaveBeenCalledWith('/bt/filters', { filters: [] })
  })
})

function makePage(overrides: Partial<BtEntriesPage> = {}): BtEntriesPage {
  return {
    items: [makeEntry()],
    total: 1,
    page: 1,
    size: 50,
    ...overrides,
  }
}

describe('BtApi — entries', () => {
  it('listEntries() GETs /bt/entries with the given days parameter and default page/size', async () => {
    const expected = makePage()
    const getJson = vi.fn().mockResolvedValue(expected)
    const api = new BtApi({ getJson } as never)

    const result = await api.listEntries(7)
    expect(getJson).toHaveBeenCalledWith('/bt/entries?days=7&page=1&size=50')
    expect(result).toEqual(expected)
  })

  it('listEntries() defaults to 7 days', async () => {
    const getJson = vi.fn().mockResolvedValue(makePage({ items: [], total: 0 }))
    const api = new BtApi({ getJson } as never)

    await api.listEntries()
    expect(getJson).toHaveBeenCalledWith('/bt/entries?days=7&page=1&size=50')
  })

  it('listEntries(days, filterId) appends &filter_id= when a filter id is given', async () => {
    const expected = makePage({ items: [makeEntry({ matched_filter_id: 3 })] })
    const getJson = vi.fn().mockResolvedValue(expected)
    const api = new BtApi({ getJson } as never)

    const result = await api.listEntries(7, 3)
    expect(getJson).toHaveBeenCalledWith('/bt/entries?days=7&page=1&size=50&filter_id=3')
    expect(result).toEqual(expected)
  })

  it('listEntries(days, filterId, page, size) forwards pagination params', async () => {
    const expected = makePage({ page: 2, size: 20 })
    const getJson = vi.fn().mockResolvedValue(expected)
    const api = new BtApi({ getJson } as never)

    const result = await api.listEntries(7, undefined, 2, 20)
    expect(getJson).toHaveBeenCalledWith('/bt/entries?days=7&page=2&size=20')
    expect(result).toEqual(expected)
  })

  it('listEntries(..., q) URL-encodes and appends the q param', async () => {
    const expected = makePage()
    const getJson = vi.fn().mockResolvedValue(expected)
    const api = new BtApi({ getJson } as never)

    await api.listEntries(7, undefined, 1, 50, 'CHT&JPN')
    expect(getJson).toHaveBeenCalledWith(
      `/bt/entries?days=7&page=1&size=50&q=${encodeURIComponent('CHT&JPN')}`,
    )
  })

  it('searchEntries() GETs /bt/entries/search with the query and limit', async () => {
    const expected = [makeEntry()]
    const getJson = vi.fn().mockResolvedValue(expected)
    const api = new BtApi({ getJson } as never)

    const result = await api.searchEntries('LoliHouse', 20)
    expect(getJson).toHaveBeenCalledWith('/bt/entries/search?q=LoliHouse&limit=20')
    expect(result).toEqual(expected)
  })

  it('searchEntries() defaults to a limit of 20', async () => {
    const getJson = vi.fn().mockResolvedValue([])
    const api = new BtApi({ getJson } as never)

    await api.searchEntries('foo')
    expect(getJson).toHaveBeenCalledWith('/bt/entries/search?q=foo&limit=20')
  })

  it('searchEntries() URL-encodes the query string', async () => {
    const getJson = vi.fn().mockResolvedValue([])
    const api = new BtApi({ getJson } as never)

    await api.searchEntries('CHT&JPN 1080p', 20)
    expect(getJson).toHaveBeenCalledWith(
      `/bt/entries/search?q=${encodeURIComponent('CHT&JPN 1080p')}&limit=20`,
    )
  })
})

describe('BtApi — filter match count', () => {
  it('filterMatchCount() POSTs /bt/filters/match-count with the keywords', async () => {
    const expected = { count: 42, over_cap: false }
    const postJson = vi.fn().mockResolvedValue(expected)
    const api = new BtApi({ postJson } as never)

    const result = await api.filterMatchCount(['LoliHouse', '1080'])
    expect(postJson).toHaveBeenCalledWith('/bt/filters/match-count', {
      keywords: ['LoliHouse', '1080'],
    })
    expect(result).toEqual(expected)
  })

  it('filterMatchCount() surfaces over_cap when the backend caps the scan', async () => {
    const postJson = vi.fn().mockResolvedValue({ count: 10000, over_cap: true })
    const api = new BtApi({ postJson } as never)

    const result = await api.filterMatchCount(['1080'])
    expect(result.over_cap).toBe(true)
  })

  it('filterMatchCount() supports an empty keyword list', async () => {
    const postJson = vi.fn().mockResolvedValue({ count: 0, over_cap: false })
    const api = new BtApi({ postJson } as never)

    await api.filterMatchCount([])
    expect(postJson).toHaveBeenCalledWith('/bt/filters/match-count', { keywords: [] })
  })
})
