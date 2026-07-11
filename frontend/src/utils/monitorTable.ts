/**
 * Pure sort/filter helpers for MonitorTable.vue.
 *
 * Kept as plain, dependency-free functions so they can be unit-tested
 * directly (see tests/components/monitor/MonitorTable.spec.ts) without
 * needing the stubbed <el-table>/<el-table-column> to simulate real
 * Element Plus sort/filter-popper UI interaction.
 */

import type { TaskProgressEntry } from '@/types'

/**
 * Compares two nullable numbers so that `null`/`undefined` always sinks to
 * the bottom, regardless of sort direction — a `null` speed/ETA is "no
 * data", not "slowest"/"soonest", so it must never look sorted-in.
 *
 * Ascending: real numbers ascend, then nulls.
 */
export function compareNullableAsc(
  a: number | null | undefined,
  b: number | null | undefined,
): number {
  const aNull = a == null
  const bNull = b == null
  if (aNull && bNull) return 0
  if (aNull) return 1
  if (bNull) return -1
  return a - b
}

/**
 * Descending: real numbers descend, then nulls (still last, not first).
 */
export function compareNullableDesc(
  a: number | null | undefined,
  b: number | null | undefined,
): number {
  const aNull = a == null
  const bNull = b == null
  if (aNull && bNull) return 0
  if (aNull) return 1
  if (bNull) return -1
  return b - a
}

/**
 * Rows sorted by speed_mbps DESC (fastest first), nulls last.
 *
 * Not the table's default order — MonitorTable.vue defaults to started_at
 * DESC (newest task first) instead; this remains available for callers that
 * specifically want a speed-ranked view.
 */
export function sortBySpeedDesc(rows: TaskProgressEntry[]): TaskProgressEntry[] {
  return [...rows].sort((a, b) => compareNullableDesc(a.speed_mbps, b.speed_mbps))
}

/**
 * Formats a duration in seconds as zero-padded `mm:ss` (e.g. `07:05`).
 * Returns '—' for null/undefined/negative/non-finite input.
 */
export function formatEtaClock(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0 || !Number.isFinite(seconds)) return '—'
  const total = Math.floor(seconds)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

/** Formats MB/s speed as `"12.3 MB/s"`, or '—' when null/undefined. */
export function formatSpeed(mbps: number | null | undefined): string {
  if (mbps == null) return '—'
  return `${mbps.toFixed(1)} MB/s`
}

/**
 * Column-filter predicate for the 來源 column. `row.source` of
 * `null`/`undefined` is normalized to `'animad'` — see sourceBadge.ts for
 * why that's the correct default rather than a distinct "unknown" bucket.
 */
export function filterBySource(row: TaskProgressEntry, value: string): boolean {
  return (row.source ?? 'animad') === value
}

/** Column-filter predicate for the 狀態 column. */
export function filterByStatus(row: TaskProgressEntry, value: string): boolean {
  return row.status === value
}

/** Column-filter predicate for the 擁有者 column. */
export function filterByOwner(row: TaskProgressEntry, value: string): boolean {
  return row.owner_username === value
}

export interface FilterOption {
  text: string
  value: string
}

/**
 * Builds a dynamic `:filters` list (Element Plus's `{text, value}[]` shape)
 * from the distinct values of `rows`, in first-seen order.
 */
export function buildFilterOptions(
  rows: TaskProgressEntry[],
  valueOf: (row: TaskProgressEntry) => string,
  labelOf: (value: string) => string = (value) => value,
): FilterOption[] {
  const seen = new Set<string>()
  const options: FilterOption[] = []
  for (const row of rows) {
    const value = valueOf(row)
    if (!seen.has(value)) {
      seen.add(value)
      options.push({ text: labelOf(value), value })
    }
  }
  return options
}
