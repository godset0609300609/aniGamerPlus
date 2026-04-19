import { categorize, isWithinLastNDays } from "@/composables/useTaskCategory";
import { afterEach, describe, expect, it, vi } from "vitest";

describe("categorize", () => {
  it("returns downloading for 正在下載", () => {
    expect(categorize("正在下載")).toBe("downloading");
  });

  it("returns downloading for 正在解密合併", () => {
    expect(categorize("正在解密合併")).toBe("downloading");
  });

  it("returns downloading for 正在上傳", () => {
    expect(categorize("正在上傳")).toBe("downloading");
  });

  it("returns downloading for retry status 任務失敗, 等待重啓", () => {
    expect(categorize("任務失敗, 等待重啓")).toBe("downloading");
  });

  it("returns downloading for retry status 失敗! 重啓中", () => {
    expect(categorize("失敗! 重啓中")).toBe("downloading");
  });

  it("returns waiting for 等待下載", () => {
    expect(categorize("等待下載")).toBe("waiting");
  });

  it("returns waiting for 正在解析", () => {
    expect(categorize("正在解析")).toBe("waiting");
  });

  it("returns completed for 下載完成", () => {
    expect(categorize("下載完成")).toBe("completed");
  });

  it("returns completed for 上傳完成", () => {
    expect(categorize("上傳完成")).toBe("completed");
  });

  it("returns completed for 任務完成", () => {
    expect(categorize("任務完成")).toBe("completed");
  });

  it("test_categorize_failure_is_completed: returns completed for 失敗", () => {
    expect(categorize("失敗")).toBe("completed");
  });

  it("returns other for unknown status", () => {
    expect(categorize("正在合並")).toBe("other");
  });

  it("returns other for empty string", () => {
    expect(categorize("")).toBe("other");
  });
});

describe("isWithinLastNDays", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns false for null", () => {
    expect(isWithinLastNDays(null, 7)).toBe(false);
  });

  it("returns false for undefined", () => {
    expect(isWithinLastNDays(undefined, 7)).toBe(false);
  });

  it("returns false for invalid date string", () => {
    expect(isWithinLastNDays("not-a-date", 7)).toBe(false);
  });

  it("returns true for a date within 7 days", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));

    const threeDAysAgo = "2026-04-15T12:00:00Z";
    expect(isWithinLastNDays(threeDAysAgo, 7)).toBe(true);
  });

  it("returns false for a date older than 7 days", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));

    const eightDaysAgo = "2026-04-10T11:59:59Z";
    expect(isWithinLastNDays(eightDaysAgo, 7)).toBe(false);
  });

  it("returns true for exactly now", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));

    expect(isWithinLastNDays("2026-04-18T12:00:00Z", 7)).toBe(true);
  });

  it("returns true for date exactly 7 days ago (boundary)", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));

    // exactly 7 * 24 * 60 * 60 * 1000 ms ago — should be true (<=)
    const exactly7DaysAgo = "2026-04-11T12:00:00Z";
    expect(isWithinLastNDays(exactly7DaysAgo, 7)).toBe(true);
  });

  it("returns false for date just over 7 days ago", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));

    const justOver7Days = "2026-04-11T11:59:59Z";
    expect(isWithinLastNDays(justOver7Days, 7)).toBe(false);
  });
});
