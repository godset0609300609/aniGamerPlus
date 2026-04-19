import { describe, expect, it, vi } from 'vitest'
import { AnimeListApi } from '@/api/animelist'
import type { AnimeListEntry, AnimeListPayload } from '@/types'

function makeEntry(overrides: Partial<AnimeListEntry> = {}): AnimeListEntry {
  return {
    sn: 12345,
    enabled: true,
    mode: 'latest',
    tag: '',
    season: 1,
    custom_name: null,
    comment: '',
    anime_name: null,
    downloaded_episodes: 0,
    known_episodes: 0,
    ...overrides,
  }
}

describe('AnimeListApi', () => {
  it('list() GETs /anime-list and returns the parsed payload', async () => {
    const expected: AnimeListPayload = {
      entries: [
        makeEntry({ sn: 111, tag: '本季新番' }),
        makeEntry({ sn: 222, anime_name: '範例番劇', known_episodes: 12, downloaded_episodes: 3 }),
      ],
    }
    const getJson = vi.fn().mockResolvedValue(expected)
    const api = new AnimeListApi({ getJson } as never)

    const result = await api.list()
    expect(getJson).toHaveBeenCalledWith('/anime-list')
    expect(result.entries).toHaveLength(2)
    expect(result.entries[0].sn).toBe(111)
    expect(result.entries[1].anime_name).toBe('範例番劇')
  })

  it('replaceAll() PUTs /anime-list wrapping entries in { entries }', async () => {
    const putJson = vi.fn().mockResolvedValue({ status: 'ok' })
    const api = new AnimeListApi({ putJson } as never)

    const entries = [
      makeEntry({ sn: 1, enabled: false, mode: null }),
      makeEntry({ sn: 2, tag: '本季新番', season: 2, comment: '備註' }),
    ]
    const result = await api.replaceAll(entries)
    expect(putJson).toHaveBeenCalledWith('/anime-list', { entries })
    expect(result.status).toBe('ok')
  })

  it('replaceAll() supports sending an empty list', async () => {
    const putJson = vi.fn().mockResolvedValue({ status: 'ok' })
    const api = new AnimeListApi({ putJson } as never)

    await api.replaceAll([])
    expect(putJson).toHaveBeenCalledWith('/anime-list', { entries: [] })
  })
})
