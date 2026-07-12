import { describe, expect, it } from 'vitest'
import {
  BT_STATUS_LIFECYCLE,
  REMOTE_CLEARED_STATUS,
  REMOTE_REMOVED_STATUS,
  resolveLabel,
  resolveTagClass,
  resolveTagType,
  resolveTooltip,
} from '@/utils/btStatus'

describe('resolveTagType / resolveLabel — full Put.io status lifecycle', () => {
  it.each([
    ['IN_QUEUE', 'info', '排隊中'],
    ['WAITING', 'info', '等待中'],
    ['PREPARING_DOWNLOAD', 'warning', '準備中'],
    ['DOWNLOADING', 'warning', '下載中'],
    ['COMPLETING', 'warning', '完成中'],
    ['SEEDING', 'primary', '做種中'],
    ['COMPLETED', 'success', '已完成'],
    ['ERROR', 'danger', '失敗'],
  ])('status=%s -> type=%s, label=%s', (status, expectedType, expectedLabel) => {
    expect(resolveTagType(status)).toBe(expectedType)
    expect(resolveLabel(status)).toBe(expectedLabel)
  })

  it('null status resolves to an empty tag type and the 未派送 label', () => {
    expect(resolveTagType(null)).toBe('')
    expect(resolveLabel(null)).toBe('未派送')
  })

  it('an unmapped/unknown status falls back to an empty tag type and its raw value as label', () => {
    expect(resolveTagType('BOGUS_STATUS')).toBe('')
    expect(resolveLabel('BOGUS_STATUS')).toBe('BOGUS_STATUS')
  })

  it('BT_STATUS_LIFECYCLE lists all 8 statuses in lifecycle order', () => {
    expect(BT_STATUS_LIFECYCLE).toEqual([
      'IN_QUEUE',
      'WAITING',
      'PREPARING_DOWNLOAD',
      'DOWNLOADING',
      'COMPLETING',
      'SEEDING',
      'COMPLETED',
      'ERROR',
    ])
  })
})

describe('post-landing remote-cleanup statuses', () => {
  it('遠端已清理 resolves to the plain tag type, its own label, a custom class, and a tooltip', () => {
    expect(resolveTagType(REMOTE_CLEARED_STATUS)).toBe('')
    expect(resolveLabel(REMOTE_CLEARED_STATUS)).toBe('遠端已清理')
    expect(resolveTagClass(REMOTE_CLEARED_STATUS)).toBe('ag-tag-remote-cleared')
    expect(resolveTooltip(REMOTE_CLEARED_STATUS)).toBe('已在遠端刪除以節省空間，本地檔案仍在')
  })

  it('遠端已移除 resolves to the info tag type, its own label, no custom class, and a tooltip', () => {
    expect(resolveTagType(REMOTE_REMOVED_STATUS)).toBe('info')
    expect(resolveLabel(REMOTE_REMOVED_STATUS)).toBe('遠端已移除')
    expect(resolveTagClass(REMOTE_REMOVED_STATUS)).toBe('')
    expect(resolveTooltip(REMOTE_REMOVED_STATUS)).toBe('Put.io 端偵測到檔案不存在（可能被自動清理或使用者手動刪除）')
  })

  it('resolveTagClass / resolveTooltip return empty/null for null and ordinary statuses', () => {
    expect(resolveTagClass(null)).toBe('')
    expect(resolveTooltip(null)).toBeNull()
    expect(resolveTagClass('COMPLETED')).toBe('')
    expect(resolveTooltip('COMPLETED')).toBeNull()
  })
})
