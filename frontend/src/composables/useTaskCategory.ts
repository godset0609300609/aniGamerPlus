/**
 * Task status categorization for the three-column monitor UI.
 */

const CATEGORY_DOWNLOADING = new Set([
  "正在下載",
  "正在解密合併",
  "正在上傳",
  "任務失敗, 等待重啓",
  "失敗! 重啓中",
  // BT downloader (Put.io -> local disk) — see backend/app/bt_downloader/landing_worker.py
  // and services/bt_downloader_service.py for the status strings published to ProgressBus.
  "Put.io 下載中",
  "準備落地",
  "準備落地 (Seeding)",
  "落地中",
  // TG downloader (chat media watcher) — see
  // backend/app/tg_downloader/downloader.py's TgDownloadWatcher._start_progress,
  // which starts every task with status='下載中' (distinct from animad's '正在下載').
  "下載中",
]);
const CATEGORY_WAITING = new Set(["等待下載", "正在解析", "等待 Put.io", "Put.io 排隊中"]);
// '中斷' is ProgressBus.finish()'s coercion target for any task that died
// mid-flight without reaching a recognised terminal status (exception, kill
// signal, or a stale Put.io transfer reset — see ALREADY_TERMINAL /
// finish() in backend/app/downloader/progress.py). It is a final state, not
// an in-progress one, so it belongs in "completed" alongside 失敗.
const CATEGORY_COMPLETED = new Set(["下載完成", "上傳完成", "任務完成", "失敗", "中斷"]);

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
