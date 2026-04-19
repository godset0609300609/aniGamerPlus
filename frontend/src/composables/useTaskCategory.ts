/**
 * Task status categorization for the three-column monitor UI.
 */

const CATEGORY_DOWNLOADING = new Set(["正在下載", "正在解密合併", "正在上傳", "任務失敗, 等待重啓", "失敗! 重啓中"]);
const CATEGORY_WAITING = new Set(["等待下載", "正在解析"]);
const CATEGORY_COMPLETED = new Set(["下載完成", "上傳完成", "任務完成", "失敗"]);

export type TaskCategory = "downloading" | "waiting" | "completed" | "other";

/**
 * Maps a task status string to one of four categories.
 * 'other' covers unknown statuses.
 */
export function categorize(status: string): TaskCategory {
  if (CATEGORY_DOWNLOADING.has(status)) return "downloading";
  if (CATEGORY_WAITING.has(status)) return "waiting";
  if (CATEGORY_COMPLETED.has(status)) return "completed";
  return "other";
}

/**
 * Returns true if the ISO datetime string falls within the last N days.
 */
export function isWithinLastNDays(iso: string | null | undefined, n: number): boolean {
  if (!iso) return false;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return false;
  return Date.now() - t <= n * 24 * 60 * 60 * 1000;
}
