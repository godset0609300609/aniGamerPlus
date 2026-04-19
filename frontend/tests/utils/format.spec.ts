import { describe, expect, it, vi, afterEach } from 'vitest'
import { formatEta, formatRelative } from '@/utils/format'

describe('formatEta', () => {
  it('returns empty string for null', () => {
    expect(formatEta(null)).toBe('')
  })

  it('returns empty string for undefined', () => {
    expect(formatEta(undefined)).toBe('')
  })

  it('returns empty string for negative seconds', () => {
    expect(formatEta(-1)).toBe('')
  })

  it('formats seconds only when < 60', () => {
    expect(formatEta(45)).toBe('45s')
    expect(formatEta(0)).toBe('0s')
    expect(formatEta(59)).toBe('59s')
  })

  it('formats minutes and seconds when >= 60 and < 3600', () => {
    expect(formatEta(60)).toBe('1m 0s')
    expect(formatEta(80)).toBe('1m 20s')
    expect(formatEta(3599)).toBe('59m 59s')
  })

  it('formats hours and minutes when >= 3600', () => {
    expect(formatEta(3600)).toBe('1h 0m')
    expect(formatEta(8100)).toBe('2h 15m')
    expect(formatEta(7200)).toBe('2h 0m')
  })
})

describe('formatRelative', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('returns empty string for null', () => {
    expect(formatRelative(null)).toBe('')
  })

  it('returns empty string for undefined', () => {
    expect(formatRelative(undefined)).toBe('')
  })

  it('returns empty string for invalid date string', () => {
    expect(formatRelative('not-a-date')).toBe('')
  })

  it('formats seconds ago when < 60 seconds', () => {
    vi.useFakeTimers()
    const now = new Date('2026-04-18T12:00:30Z')
    vi.setSystemTime(now)
    const started = new Date('2026-04-18T12:00:00Z').toISOString()
    const result = formatRelative(started)
    // Should contain "秒" reference
    expect(result).toContain('開始於')
    expect(result).toContain('秒')
  })

  it('formats minutes ago when >= 60 seconds', () => {
    vi.useFakeTimers()
    const now = new Date('2026-04-18T12:05:00Z')
    vi.setSystemTime(now)
    const started = new Date('2026-04-18T12:00:00Z').toISOString()
    const result = formatRelative(started)
    expect(result).toContain('開始於')
    expect(result).toContain('分鐘')
  })

  it('formats hours ago when >= 3600 seconds', () => {
    vi.useFakeTimers()
    const now = new Date('2026-04-18T14:00:00Z')
    vi.setSystemTime(now)
    const started = new Date('2026-04-18T12:00:00Z').toISOString()
    const result = formatRelative(started)
    expect(result).toContain('開始於')
    expect(result).toContain('小時')
  })
})
