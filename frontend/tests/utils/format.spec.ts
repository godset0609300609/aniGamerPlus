import { describe, expect, it, vi, afterEach } from 'vitest'
import { clampPercentage, formatEta, formatRelative, formatRelativeBare } from '@/utils/format'

describe('clampPercentage', () => {
  // Backend progress writers use inconsistent scales: animad's
  // segment_downloader emits 0-100, while ffmpeg / BT-landing / TG-download
  // emit a 0-1 fraction. clampPercentage must normalize both to 0-100.

  it('treats a 0-1 fraction as a fraction — the BT/TG bug (rate=0.6 must render as 60%, not 1%)', () => {
    expect(clampPercentage(0.6)).toBe(60)
  })

  it('treats 0.0 as 0%', () => {
    expect(clampPercentage(0.0)).toBe(0)
  })

  it('documented edge case: a literal 1 is read as 100%, not 1%', () => {
    expect(clampPercentage(1)).toBe(100)
  })

  it('leaves an already-0-100-scale value unaffected (animad segment_downloader)', () => {
    expect(clampPercentage(60)).toBe(60)
  })

  it('leaves 100 unaffected', () => {
    expect(clampPercentage(100)).toBe(100)
  })

  it('clamps values above 100 down to 100', () => {
    expect(clampPercentage(150)).toBe(100)
  })

  it('clamps negative values up to 0', () => {
    expect(clampPercentage(-5)).toBe(0)
  })
})

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

  it('still returns the "開始於" prefix (TaskCard relies on this)', () => {
    vi.useFakeTimers()
    const now = new Date('2026-04-18T12:09:00Z')
    vi.setSystemTime(now)
    const started = new Date('2026-04-18T12:00:00Z').toISOString()
    expect(formatRelative(started)).toBe(`開始於 ${formatRelativeBare(started)}`)
  })
})

describe('formatRelativeBare', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('returns empty string for null', () => {
    expect(formatRelativeBare(null)).toBe('')
  })

  it('returns empty string for undefined', () => {
    expect(formatRelativeBare(undefined)).toBe('')
  })

  it('returns empty string for invalid date string', () => {
    expect(formatRelativeBare('not-a-date')).toBe('')
  })

  it('formats seconds ago when < 60 seconds, with no "開始於" prefix', () => {
    vi.useFakeTimers()
    const now = new Date('2026-04-18T12:00:30Z')
    vi.setSystemTime(now)
    const started = new Date('2026-04-18T12:00:00Z').toISOString()
    const result = formatRelativeBare(started)
    expect(result).not.toContain('開始於')
    expect(result).toContain('秒')
  })

  it('formats "N 分鐘前" for minutes ago', () => {
    vi.useFakeTimers()
    const now = new Date('2026-04-18T12:09:00Z')
    vi.setSystemTime(now)
    const started = new Date('2026-04-18T12:00:00Z').toISOString()
    expect(formatRelativeBare(started)).toBe('9 分鐘前')
  })

  it('formats "N 小時前" for hours ago', () => {
    vi.useFakeTimers()
    const now = new Date('2026-04-18T14:00:00Z')
    vi.setSystemTime(now)
    const started = new Date('2026-04-18T12:00:00Z').toISOString()
    expect(formatRelativeBare(started)).toBe('2 小時前')
  })

  it('formats "N 天前" for days ago', () => {
    vi.useFakeTimers()
    const now = new Date('2026-04-21T12:00:00Z')
    vi.setSystemTime(now)
    const started = new Date('2026-04-18T12:00:00Z').toISOString()
    expect(formatRelativeBare(started)).toBe('3 天前')
  })
})
