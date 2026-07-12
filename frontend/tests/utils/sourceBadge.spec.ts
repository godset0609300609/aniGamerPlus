import { describe, expect, it } from 'vitest'
import { sourceBadgeColor, sourceBadgeInfo, sourceBadgeLabel, sourceBadgeTextColor } from '@/utils/sourceBadge'

describe('sourceBadgeInfo', () => {
  it('maps null to a neutral 未知 badge, NOT 動畫瘋', () => {
    // Regression guard: force_finish() ghost-reconciliation entries and any
    // other source-less progress payload used to be treated identically to
    // 'animad' here, which is how a completed Telegram download ended up
    // rendering a duplicate card mislabeled 動畫瘋 on the monitor. Every live
    // download path now sets an explicit source, so null genuinely means
    // "unknown", not "animad".
    const info = sourceBadgeInfo(null)
    expect(info.key).toBe('unknown')
    expect(info.label).toBe('未知')
    expect(info.label).not.toBe('動畫瘋')
  })

  it('maps undefined the same as null', () => {
    const info = sourceBadgeInfo(undefined)
    expect(info.key).toBe('unknown')
    expect(info.label).toBe('未知')
  })

  it('maps animad to the Bahamut-green 動畫瘋 badge', () => {
    const info = sourceBadgeInfo('animad')
    expect(info.key).toBe('animad')
    expect(info.label).toBe('動畫瘋')
    expect(info.color).toBe('#3b8686')
    expect(info.textColor).toBe('#fff')
  })

  it('maps tg to the Telegram-blue badge', () => {
    const info = sourceBadgeInfo('tg')
    expect(info.key).toBe('tg')
    expect(info.label).toBe('Telegram')
    expect(info.color).toBe('#0088cc')
    expect(info.textColor).toBe('#fff')
  })

  it('maps bt to the orange BT badge', () => {
    const info = sourceBadgeInfo('bt')
    expect(info.key).toBe('bt')
    expect(info.label).toBe('BT')
    expect(info.color).toBe('#e6a23c')
    expect(info.textColor).toBe('#fff')
  })

  it('maps bilibili to the Bilibili-blue badge', () => {
    const info = sourceBadgeInfo('bilibili')
    expect(info.key).toBe('bilibili')
    expect(info.label).toBe('Bilibili')
    expect(info.color).toBe('#00a1d6')
    expect(info.textColor).toBe('#fff')
  })

  it('falls back to a neutral badge labeled with the raw value for an unrecognized source', () => {
    const info = sourceBadgeInfo('weird')
    expect(info.key).toBe('other')
    expect(info.label).toBe('weird')
    expect(info.color).toBe('var(--el-fill-color-dark)')
    expect(info.textColor).toBe('var(--el-text-color-primary)')
  })
})

describe('sourceBadgeColor / sourceBadgeLabel / sourceBadgeTextColor', () => {
  it('are thin wrappers that read the corresponding field off sourceBadgeInfo', () => {
    expect(sourceBadgeColor('tg')).toBe(sourceBadgeInfo('tg').color)
    expect(sourceBadgeLabel('tg')).toBe(sourceBadgeInfo('tg').label)
    expect(sourceBadgeTextColor('tg')).toBe(sourceBadgeInfo('tg').textColor)
  })

  it('sourceBadgeLabel(null) returns 未知, not 動畫瘋', () => {
    expect(sourceBadgeLabel(null)).toBe('未知')
  })
})
