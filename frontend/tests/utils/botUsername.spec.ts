import { describe, expect, it } from 'vitest'
import { isValidBotUsername, normalizeBotUsername } from '@/utils/botUsername'

describe('normalizeBotUsername', () => {
  it('prepends @ when missing', () => {
    expect(normalizeBotUsername('aniGamerPlusBot')).toBe('@aniGamerPlusBot')
  })

  it('leaves an already-prefixed handle untouched', () => {
    expect(normalizeBotUsername('@aniGamerPlusBot')).toBe('@aniGamerPlusBot')
  })

  it('trims leading/trailing whitespace', () => {
    expect(normalizeBotUsername('  aniGamerPlusBot  ')).toBe('@aniGamerPlusBot')
  })

  it('strips a https://t.me/ prefix and converts to @', () => {
    expect(normalizeBotUsername('https://t.me/aniGamerPlusBot')).toBe('@aniGamerPlusBot')
  })

  it('strips a http://t.me/ prefix', () => {
    expect(normalizeBotUsername('http://t.me/aniGamerPlusBot')).toBe('@aniGamerPlusBot')
  })

  it('strips a bare t.me/ prefix', () => {
    expect(normalizeBotUsername('t.me/aniGamerPlusBot')).toBe('@aniGamerPlusBot')
  })

  it('strips a t.me/ prefix case-insensitively', () => {
    expect(normalizeBotUsername('HTTPS://T.ME/aniGamerPlusBot')).toBe('@aniGamerPlusBot')
  })

  it('trims whitespace around a t.me link before stripping the prefix', () => {
    expect(normalizeBotUsername('  https://t.me/aniGamerPlusBot  ')).toBe('@aniGamerPlusBot')
  })

  it('returns an empty string for empty input', () => {
    expect(normalizeBotUsername('')).toBe('')
  })

  it('returns an empty string for whitespace-only input', () => {
    expect(normalizeBotUsername('   ')).toBe('')
  })

  it('does not turn a bare t.me/ (no handle) into a lone @', () => {
    expect(normalizeBotUsername('t.me/')).toBe('')
  })
})

describe('isValidBotUsername', () => {
  it('accepts a well-formed handle', () => {
    expect(isValidBotUsername('@aniGamerPlusBot')).toBe(true)
  })

  it('accepts the minimum 4-character handle', () => {
    expect(isValidBotUsername('@abcd')).toBe(true)
  })

  it('accepts a 32-character handle', () => {
    expect(isValidBotUsername(`@${'a'.repeat(32)}`)).toBe(true)
  })

  it('rejects a missing @ prefix', () => {
    expect(isValidBotUsername('aniGamerPlusBot')).toBe(false)
  })

  it('rejects a handle shorter than 4 characters', () => {
    expect(isValidBotUsername('@abc')).toBe(false)
  })

  it('rejects a handle longer than 32 characters', () => {
    expect(isValidBotUsername(`@${'a'.repeat(33)}`)).toBe(false)
  })

  it('rejects embedded spaces', () => {
    expect(isValidBotUsername('@has space')).toBe(false)
  })

  it('rejects a t.me URL that was not normalized first', () => {
    expect(isValidBotUsername('https://t.me/aniGamerPlusBot')).toBe(false)
  })

  it('rejects an empty string', () => {
    expect(isValidBotUsername('')).toBe(false)
  })
})
