import { describe, expect, it } from 'vitest'
import { tokenizeTitle } from '@/utils/tokenize'

describe('tokenizeTitle', () => {
  it('extracts every square-bracket group when the whole title is brackets', () => {
    expect(tokenizeTitle('[Skytree][ONE PIECE][1109][BIG5][MP4][1080P]')).toEqual({
      bracket: ['Skytree', 'ONE PIECE', '1109', 'BIG5', 'MP4', '1080P'],
      freeText: [],
    })
  })

  it('splits free text between bracket groups on whitespace/hyphen', () => {
    expect(
      tokenizeTitle(
        '[LoliHouse] Hikaru ga Shinda Natsu - 08 [WebRip 1080p HEVC-10bit AAC][CHT&JPN]',
      ),
    ).toEqual({
      bracket: ['LoliHouse', 'WebRip 1080p HEVC-10bit AAC', 'CHT&JPN'],
      freeText: ['Hikaru', 'ga', 'Shinda', 'Natsu', '08'],
    })
  })

  it('supports full-width 【...】 brackets', () => {
    expect(tokenizeTitle('【幻櫻字幕組】【4月新番】')).toEqual({
      bracket: ['幻櫻字幕組', '4月新番'],
      freeText: [],
    })
  })

  it('returns empty groups for an empty string', () => {
    expect(tokenizeTitle('')).toEqual({ bracket: [], freeText: [] })
  })

  it('drops length-1 tokens from both groups', () => {
    expect(tokenizeTitle('[a] b')).toEqual({ bracket: [], freeText: [] })
  })

  it('preserves original casing', () => {
    const result = tokenizeTitle('[MP4][1080P] Title')
    expect(result.bracket).toContain('MP4')
    expect(result.bracket).toContain('1080P')
  })

  it('does not dedupe repeated tokens within a group', () => {
    expect(tokenizeTitle('[XX][XX] foo foo')).toEqual({
      bracket: ['XX', 'XX'],
      freeText: ['foo', 'foo'],
    })
  })

  it('test_bracket_content_splits_on_slash', () => {
    expect(
      tokenizeTitle('[二十世紀電氣目錄 / 20 Seiki Denki Mokuroku / Nijusseiki Denki Mokuroku]'),
    ).toEqual({
      bracket: ['二十世紀電氣目錄', '20 Seiki Denki Mokuroku', 'Nijusseiki Denki Mokuroku'],
      freeText: [],
    })
  })

  it('test_bracket_content_splits_on_pipe', () => {
    expect(tokenizeTitle('[MP4|1080P]')).toEqual({
      bracket: ['MP4', '1080P'],
      freeText: [],
    })
  })

  it('test_bracket_content_mixed_slash_and_pipe', () => {
    expect(tokenizeTitle('[aa/bb|cc]')).toEqual({
      bracket: ['aa', 'bb', 'cc'],
      freeText: [],
    })
  })

  it('test_bracket_content_preserves_internal_spaces_and_dashes', () => {
    expect(tokenizeTitle('[HEVC-10bit AAC]')).toEqual({
      bracket: ['HEVC-10bit AAC'],
      freeText: [],
    })
  })

  it('test_bracket_content_trims_and_drops_empty', () => {
    expect(tokenizeTitle('[/ / foo /]')).toEqual({
      bracket: ['foo'],
      freeText: [],
    })
  })
})
