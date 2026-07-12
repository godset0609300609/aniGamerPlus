import type { SocketState } from "@/api/ws";
import type { TaskProgressMap } from "@/types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Ref } from "vue";
import { ref } from "vue";

// ---------------------------------------------------------------------------
// Controllable ProgressSocket stub
// The stub captures the onMessage handler so tests can push payloads through.
// ---------------------------------------------------------------------------
let capturedOnMessage: ((t: TaskProgressMap) => void) | null = null;
const mockConnect = vi.fn();
const mockClose = vi.fn();

let stubState: Ref<SocketState>;
let stubShowDisconnectedBanner: Ref<boolean>;
let stubLastTasks: Ref<TaskProgressMap>;
let stubHasReceivedFirst: Ref<boolean>;

function resetSocketStubs() {
  stubState = ref<SocketState>("connecting");
  stubShowDisconnectedBanner = ref(false);
  stubLastTasks = ref<TaskProgressMap>({});
  stubHasReceivedFirst = ref(false);
  capturedOnMessage = null;
  mockConnect.mockReset();
  mockClose.mockReset();
}

vi.mock("@/api/ws", () => ({
  ProgressSocket: class {
    get state() {
      return stubState;
    }
    get showDisconnectedBanner() {
      return stubShowDisconnectedBanner;
    }
    get lastTasks() {
      return stubLastTasks;
    }
    get hasReceivedFirst() {
      return stubHasReceivedFirst;
    }
    constructor(handlers: { onMessage: (t: TaskProgressMap) => void }) {
      capturedOnMessage = handlers.onMessage;
    }
    connect = mockConnect;
    close = mockClose;
  },
}));

// Mock TasksApi so no real HTTP calls are made when connect() triggers loadHistory.
const mockFetchHistory = vi.fn().mockResolvedValue([]);
vi.mock("@/api/tasks", () => ({
  TasksApi: class {
    fetchHistory(...args: unknown[]) {
      return mockFetchHistory(...args);
    }
    submitManual() {
      return Promise.resolve({ status: "ok" });
    }
  },
  extractSn: (input: string) => input,
}));

// Import AFTER the mock is set up.
import { __resetProgressStoreForTest, HIDDEN_FROM_MONITOR, useProgressStore } from "@/stores/progress";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function pushMessage(tasks: TaskProgressMap) {
  capturedOnMessage!(tasks);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("useProgressStore — singleton", () => {
  beforeEach(() => {
    resetSocketStubs();
    __resetProgressStoreForTest();
  });

  it("returns the same instance on repeated calls", () => {
    const a = useProgressStore();
    const b = useProgressStore();
    expect(a).toBe(b);
  });

  it("__resetProgressStoreForTest creates a fresh instance", () => {
    const a = useProgressStore();
    __resetProgressStoreForTest();
    const b = useProgressStore();
    expect(a).not.toBe(b);
  });
});

describe("useProgressStore — tasks reactive state", () => {
  beforeEach(() => {
    resetSocketStubs();
    __resetProgressStoreForTest();
  });

  afterEach(() => {
    __resetProgressStoreForTest();
  });

  it("tasks starts empty", () => {
    const store = useProgressStore();
    expect(store.tasks.value).toEqual({});
  });

  it("tasks updates when socket delivers a message", () => {
    const store = useProgressStore();
    pushMessage({ "1": { sn: 1, rate: 50, status: "正在下載", filename: "a.mp4" } });
    expect(store.tasks.value).toEqual({
      "1": { sn: 1, rate: 50, status: "正在下載", filename: "a.mp4" },
    });
  });

  it("state ref reflects the socket state ref", () => {
    const store = useProgressStore();
    expect(store.state.value).toBe("connecting");
    stubState.value = "open";
    expect(store.state.value).toBe("open");
  });

  it("showDisconnectedBanner reflects the socket ref", () => {
    const store = useProgressStore();
    expect(store.showDisconnectedBanner.value).toBe(false);
    stubShowDisconnectedBanner.value = true;
    expect(store.showDisconnectedBanner.value).toBe(true);
  });

  it("hasReceivedFirst reflects the socket ref", () => {
    const store = useProgressStore();
    expect(store.hasReceivedFirst.value).toBe(false);
    stubHasReceivedFirst.value = true;
    expect(store.hasReceivedFirst.value).toBe(true);
  });
});

describe("useProgressStore — computed counts", () => {
  beforeEach(() => {
    resetSocketStubs();
    __resetProgressStoreForTest();
  });

  afterEach(() => {
    vi.useRealTimers();
    __resetProgressStoreForTest();
  });

  it("totalCount is 0 when no tasks", () => {
    const store = useProgressStore();
    expect(store.totalCount.value).toBe(0);
  });

  it("downloadingCount counts active downloading tasks", () => {
    const store = useProgressStore();
    pushMessage({
      "1": { sn: 1, rate: 50, status: "正在下載", filename: "a.mp4" },
      "2": { sn: 2, rate: 80, status: "正在解密合併", filename: "b.mp4" },
    });
    expect(store.downloadingCount.value).toBe(2);
  });

  it("waitingCount counts waiting tasks including 正在解析", () => {
    const store = useProgressStore();
    pushMessage({
      "3": { sn: 3, rate: 0, status: "等待下載", filename: "c.mp4" },
      "4": { sn: 4, rate: 0, status: "正在解析", filename: "d.mp4" },
    });
    // '正在解析' is categorised as 'waiting', same as '等待下載'.
    expect(store.waitingCount.value).toBe(2);
  });

  it("downloadingCount includes retry tasks", () => {
    const store = useProgressStore();
    pushMessage({
      "5": { sn: 5, rate: 0, status: "任務失敗, 等待重啓", filename: "e.mp4" },
      "6": { sn: 6, rate: 0, status: "失敗! 重啓中", filename: "f.mp4" },
    });
    expect(store.downloadingCount.value).toBe(2);
  });

  it("completedCount counts terminal tasks within 7 days", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));
    const store = useProgressStore();
    pushMessage({
      "7": { sn: 7, rate: 100, status: "下載完成", filename: "done.mp4", started_at: "2026-04-15T00:00:00Z" },
    });
    expect(store.completedCount.value).toBe(1);
  });

  it("terminal statuses beyond 7 days are excluded from completedCount", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));
    const store = useProgressStore();
    pushMessage({
      "8": { sn: 8, rate: 100, status: "下載完成", filename: "old.mp4", started_at: "2026-04-01T00:00:00Z" },
    });
    expect(store.completedCount.value).toBe(0);
  });

  it("terminal statuses without started_at are excluded from completedCount", () => {
    const store = useProgressStore();
    pushMessage({
      "9": { sn: 9, rate: 100, status: "任務完成", filename: "no-date.mp4" },
    });
    expect(store.completedCount.value).toBe(0);
  });

  it("totalCount excludes completed entries (only downloading + waiting)", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));
    const store = useProgressStore();
    pushMessage({
      "1": { sn: 1, rate: 50, status: "正在下載", filename: "dl.mp4" },
      "2": { sn: 2, rate: 0, status: "等待下載", filename: "wait.mp4" },
      "3": { sn: 3, rate: 100, status: "下載完成", filename: "done.mp4", started_at: "2026-04-15T00:00:00Z" },
    });
    // completedCount === 1 but totalCount must only reflect downloading + waiting
    expect(store.completedCount.value).toBe(1);
    expect(store.totalCount.value).toBe(2);
  });
});

describe("useProgressStore — byCategory", () => {
  beforeEach(() => {
    resetSocketStubs();
    __resetProgressStoreForTest();
  });

  afterEach(() => {
    vi.useRealTimers();
    __resetProgressStoreForTest();
  });

  it("partitions tasks into correct categories", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));
    const store = useProgressStore();
    pushMessage({
      "1": { sn: 1, rate: 50, status: "正在下載", filename: "dl.mp4" },
      "2": { sn: 2, rate: 0, status: "等待下載", filename: "wait.mp4" },
      "3": { sn: 3, rate: 100, status: "下載完成", filename: "done.mp4", started_at: "2026-04-15T00:00:00Z" },
    });
    const cat = store.byCategory.value;
    expect(cat.downloading.map((e) => e.filename)).toContain("dl.mp4");
    expect(cat.waiting.map((e) => e.filename)).toContain("wait.mp4");
    expect(cat.completed.map((e) => e.filename)).toContain("done.mp4");
  });

  it('"other" status tasks land in downloading', () => {
    const store = useProgressStore();
    pushMessage({
      "10": { sn: 10, rate: 90, status: "正在合並", filename: "other.mp4" },
    });
    expect(store.byCategory.value.downloading.map((e) => e.filename)).toContain("other.mp4");
    expect(store.byCategory.value.waiting.length).toBe(0);
    expect(store.byCategory.value.completed.length).toBe(0);
  });

  it("retry status tasks land in downloading column", () => {
    const store = useProgressStore();
    pushMessage({
      "11": { sn: 11, rate: 0, status: "任務失敗, 等待重啓", filename: "retry.mp4" },
      "12": { sn: 12, rate: 0, status: "失敗! 重啓中", filename: "retry2.mp4" },
    });
    const cat = store.byCategory.value;
    expect(cat.downloading.map((e) => e.filename)).toContain("retry.mp4");
    expect(cat.downloading.map((e) => e.filename)).toContain("retry2.mp4");
    expect(cat.waiting.length).toBe(0);
    expect(cat.completed.length).toBe(0);
  });

  it("completed tasks older than 7 days do not appear in completed", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));
    const store = useProgressStore();
    pushMessage({
      "13": { sn: 13, rate: 100, status: "下載完成", filename: "old.mp4", started_at: "2026-04-01T00:00:00Z" },
    });
    expect(store.byCategory.value.completed.length).toBe(0);
  });

  it("失敗 task lands in byCategory.completed and not in downloading", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));
    const store = useProgressStore();
    pushMessage({
      "20": {
        sn: 20,
        rate: 0,
        status: "失敗",
        filename: "failed.mp4",
        started_at: "2026-04-17T00:00:00Z",
        finished_at: "2026-04-17T01:00:00Z",
      },
    });
    const cat = store.byCategory.value;
    expect(cat.completed.map((e) => e.filename)).toContain("failed.mp4");
    expect(cat.downloading.map((e) => e.filename)).not.toContain("failed.mp4");
    expect(cat.waiting.map((e) => e.filename)).not.toContain("failed.mp4");
  });

  it("失敗 task is NOT in activeEntries", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));
    const store = useProgressStore();
    pushMessage({
      "21": {
        sn: 21,
        rate: 0,
        status: "失敗",
        filename: "failed2.mp4",
        started_at: "2026-04-17T00:00:00Z",
        finished_at: "2026-04-17T01:00:00Z",
      },
    });
    expect(store.activeEntries.value.map((e) => e.filename)).not.toContain("failed2.mp4");
    expect(store.downloadingCount.value).toBe(0);
  });
});

describe("useProgressStore — completedEntries", () => {
  beforeEach(() => {
    resetSocketStubs();
    __resetProgressStoreForTest();
  });

  afterEach(() => {
    vi.useRealTimers();
    __resetProgressStoreForTest();
  });

  it("completedEntries includes terminal tasks within 7 days", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));
    const store = useProgressStore();
    pushMessage({
      "1": { sn: 1, rate: 100, status: "下載完成", filename: "done.mp4", started_at: "2026-04-15T00:00:00Z" },
    });
    expect(store.completedEntries.value.map((e) => e.filename)).toContain("done.mp4");
  });

  it("completedEntries excludes tasks started 8 days ago", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));
    const store = useProgressStore();
    // 8 days before 2026-04-18 = 2026-04-10
    pushMessage({
      "2": { sn: 2, rate: 100, status: "任務完成", filename: "old.mp4", started_at: "2026-04-10T11:59:59Z" },
    });
    expect(store.completedEntries.value.length).toBe(0);
  });

  it("completedEntries excludes non-terminal tasks", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));
    const store = useProgressStore();
    pushMessage({
      "3": { sn: 3, rate: 50, status: "正在下載", filename: "active.mp4", started_at: "2026-04-17T00:00:00Z" },
    });
    expect(store.completedEntries.value.length).toBe(0);
  });
});

describe("useProgressStore — connect / close delegation", () => {
  beforeEach(() => {
    resetSocketStubs();
    __resetProgressStoreForTest();
  });

  afterEach(() => {
    vi.useRealTimers();
    __resetProgressStoreForTest();
  });

  it("connect() delegates to the socket", () => {
    const store = useProgressStore();
    store.connect();
    expect(mockConnect).toHaveBeenCalledTimes(1);
  });

  it("close() delegates to the socket", () => {
    const store = useProgressStore();
    store.close();
    expect(mockClose).toHaveBeenCalledTimes(1);
  });

  it("close() clears the history poll timer that was started by connect()", () => {
    vi.useFakeTimers();
    const clearSpy = vi.spyOn(globalThis, "clearInterval");

    const store = useProgressStore();
    store.connect(); // sets _historyPollTimer via setInterval
    store.close();   // should clearInterval and null the timer

    expect(mockClose).toHaveBeenCalledTimes(1);
    // clearInterval must have been called at least once for the poll timer.
    expect(clearSpy).toHaveBeenCalled();
  });

  it("close() is safe to call without prior connect() (timer is null)", () => {
    const store = useProgressStore();
    // Should not throw even though _historyPollTimer is null.
    expect(() => store.close()).not.toThrow();
    expect(mockClose).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// History integration
// ---------------------------------------------------------------------------

describe("useProgressStore — historyEntries", () => {
  beforeEach(() => {
    resetSocketStubs();
    __resetProgressStoreForTest();
    mockFetchHistory.mockResolvedValue([]);
  });

  afterEach(() => {
    vi.useRealTimers();
    __resetProgressStoreForTest();
  });

  it("historyEntries starts empty", () => {
    const store = useProgressStore();
    expect(store.historyEntries.value).toEqual([]);
  });

  it("loadHistory resolves without throwing (mock returns [])", async () => {
    const store = useProgressStore();
    await expect(store.loadHistory()).resolves.toBeUndefined();
    // Mock always returns [] so historyEntries stays empty.
    expect(store.historyEntries.value).toEqual([]);
  });

  it("loadHistory swallows fetch errors (non-fatal) and keeps previous historyEntries", async () => {
    const store = useProgressStore();

    // Pre-populate historyEntries with a known value.
    store.historyEntries.value = [
      { id: 1, sn: 1, filename: "existing.mp4", final_status: "下載完成", retries: 0, finished_at: "2026-04-18T08:00:00Z" },
    ];

    // Make fetchHistory reject for this call only.
    mockFetchHistory.mockRejectedValueOnce(new Error("History API unavailable"));

    await store.loadHistory();

    // historyEntries must not be wiped — the catch block preserves existing data.
    expect(store.historyEntries.value).toHaveLength(1);
    expect(store.historyEntries.value[0]!.filename).toBe("existing.mp4");
  });

  it("byCategory.completed merges live terminal tasks when history is empty", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));

    const store = useProgressStore();
    // Push a live terminal task — history is empty (mock returns []).
    pushMessage({
      "50": {
        sn: 50,
        rate: 100,
        status: "下載完成",
        filename: "live_done.mp4",
        started_at: "2026-04-18T10:00:00Z",
        finished_at: "2026-04-18T11:00:00Z",
      },
    });

    const completed = store.byCategory.value.completed;
    // The live terminal task should appear in completed.
    expect(completed.some((e) => e.sn === 50 && e.filename === "live_done.mp4")).toBe(true);
  });

  it("live terminal tasks take precedence over history for the same sn", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));

    const store = useProgressStore();

    // Manually inject a history entry directly into the store's ref.
    store.historyEntries.value = [
      {
        id: 3,
        sn: 300,
        filename: "hist_old.mp4",
        final_status: "中斷",
        retries: 0,
        finished_at: "2026-04-18T09:00:00+00:00",
      },
    ];

    // Push a live terminal entry for the same sn.
    pushMessage({
      "300": {
        sn: 300,
        rate: 100,
        status: "下載完成",
        filename: "live.mp4",
        started_at: "2026-04-18T10:00:00Z",
        finished_at: "2026-04-18T11:00:00Z",
      },
    });

    const completed = store.byCategory.value.completed;
    const entry300 = completed.find((e) => e.sn === 300);
    // Live entry should win — filename is 'live.mp4', status '下載完成'.
    expect(entry300).toBeDefined();
    expect(entry300?.filename).toBe("live.mp4");
    // Should appear only once (not duplicated from history).
    expect(completed.filter((e) => e.sn === 300).length).toBe(1);
  });

  it("history-only entries appear in byCategory.completed", () => {
    const store = useProgressStore();

    // Inject a history entry that has no corresponding live task.
    store.historyEntries.value = [
      {
        id: 4,
        sn: 400,
        filename: "hist_only.mp4",
        final_status: "任務完成",
        retries: 1,
        finished_at: "2026-04-18T08:00:00+00:00",
      },
    ];

    const completed = store.byCategory.value.completed;
    const entry400 = completed.find((e) => e.sn === 400);
    expect(entry400).toBeDefined();
    expect(entry400?.filename).toBe("hist_only.mp4");
    expect(entry400?.status).toBe("任務完成");
  });

  it("history-only entries preserve their source field (not dropped in the merge)", () => {
    // Regression guard: a history row's `source` used to be silently
    // dropped when mapped into a TaskProgressEntry in mergedCompleted,
    // which fed sourceBadge.ts's null fallback and mislabeled every
    // DB-history-derived completed card regardless of its real source.
    const store = useProgressStore();

    store.historyEntries.value = [
      {
        id: 5,
        sn: 401,
        filename: "hist_tg.mp4",
        final_status: "下載完成",
        retries: 0,
        finished_at: "2026-04-18T08:00:00+00:00",
        source: "tg",
      },
    ];

    const completed = store.byCategory.value.completed;
    const entry401 = completed.find((e) => e.sn === 401);
    expect(entry401).toBeDefined();
    expect(entry401?.source).toBe("tg");
  });
});

// ---------------------------------------------------------------------------
// Task 1 regression — WS payload with terminal status + finished_at must
// appear in byCategory.completed immediately (no 60-second DB poll needed).
// ---------------------------------------------------------------------------

describe("useProgressStore — terminal entry appears in completed via WS (Task 1 regression)", () => {
  beforeEach(() => {
    resetSocketStubs();
    __resetProgressStoreForTest();
  });

  afterEach(() => {
    vi.useRealTimers();
    __resetProgressStoreForTest();
  });

  it("WS payload with status=下載完成 + finished_at lands in byCategory.completed instantly", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-19T12:00:00Z"));
    const store = useProgressStore();

    // Simulate the WS push that now includes terminal entries with finished_at.
    pushMessage({
      "777": {
        sn: 777,
        rate: 100,
        status: "下載完成",
        filename: "just_finished.mp4",
        started_at: "2026-04-19T11:50:00Z",
        finished_at: "2026-04-19T11:59:00Z",
      },
    });

    const completed = store.byCategory.value.completed;
    expect(completed.some((e) => e.sn === 777)).toBe(true);
    // Must not appear in active/downloading/waiting.
    expect(store.activeEntries.value.some((e) => e.sn === 777)).toBe(false);
    expect(store.byCategory.value.downloading.some((e) => e.sn === 777)).toBe(false);
    expect(store.byCategory.value.waiting.some((e) => e.sn === 777)).toBe(false);
  });

  it("WS payload with status=任務完成 + finished_at lands in byCategory.completed instantly", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-19T12:00:00Z"));
    const store = useProgressStore();

    pushMessage({
      "778": {
        sn: 778,
        rate: 100,
        status: "任務完成",
        filename: "series_done.mp4",
        started_at: "2026-04-19T10:00:00Z",
        finished_at: "2026-04-19T11:55:00Z",
      },
    });

    expect(store.byCategory.value.completed.some((e) => e.sn === 778)).toBe(true);
    expect(store.activeEntries.value.some((e) => e.sn === 778)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Cancelled task hiding (HIDDEN_FROM_MONITOR)
// ---------------------------------------------------------------------------

describe("useProgressStore — HIDDEN_FROM_MONITOR (已取消)", () => {
  beforeEach(() => {
    resetSocketStubs();
    __resetProgressStoreForTest();
  });

  afterEach(() => {
    __resetProgressStoreForTest();
  });

  it("HIDDEN_FROM_MONITOR contains 已取消", () => {
    expect(HIDDEN_FROM_MONITOR.has("已取消")).toBe(true);
  });

  it("已取消 live task does not appear in byCategory.downloading", () => {
    const store = useProgressStore();
    pushMessage({ "1": { sn: 1, rate: 0, status: "已取消", filename: "x.mp4" } });
    expect(store.byCategory.value.downloading.map((e) => e.filename)).not.toContain("x.mp4");
  });

  it("已取消 live task does not appear in byCategory.waiting", () => {
    const store = useProgressStore();
    pushMessage({ "1": { sn: 1, rate: 0, status: "已取消", filename: "x.mp4" } });
    expect(store.byCategory.value.waiting.map((e) => e.filename)).not.toContain("x.mp4");
  });

  it("已取消 live task does not appear in byCategory.completed", () => {
    const store = useProgressStore();
    pushMessage({
      "1": {
        sn: 1,
        rate: 0,
        status: "已取消",
        filename: "x.mp4",
        started_at: "2026-04-18T10:00:00Z",
        finished_at: "2026-04-18T11:00:00Z",
      },
    });
    expect(store.byCategory.value.completed.map((e) => e.filename)).not.toContain("x.mp4");
  });

  it("totalCount is 0 when the only task is 已取消", () => {
    const store = useProgressStore();
    pushMessage({ "1": { sn: 1, rate: 0, status: "已取消", filename: "x.mp4" } });
    expect(store.totalCount.value).toBe(0);
  });

  it("已取消 history entry does not appear in mergedCompleted", () => {
    const store = useProgressStore();

    store.historyEntries.value = [
      {
        id: 99,
        sn: 999,
        filename: "cancelled_hist.mp4",
        final_status: "已取消",
        retries: 0,
        finished_at: "2026-04-18T08:00:00+00:00",
      },
    ];

    const completed = store.byCategory.value.completed;
    expect(completed.map((e) => e.filename)).not.toContain("cancelled_hist.mp4");
  });
});

// ---------------------------------------------------------------------------
// Interrupted task hiding (HIDDEN_FROM_MONITOR — 中斷)
// ---------------------------------------------------------------------------

describe("useProgressStore — HIDDEN_FROM_MONITOR (中斷)", () => {
  beforeEach(() => {
    resetSocketStubs();
    __resetProgressStoreForTest();
  });

  afterEach(() => {
    __resetProgressStoreForTest();
  });

  it("HIDDEN_FROM_MONITOR contains 中斷", () => {
    expect(HIDDEN_FROM_MONITOR.has("中斷")).toBe(true);
  });

  it("中斷 live task does not appear in byCategory.downloading", () => {
    const store = useProgressStore();
    pushMessage({ "1": { sn: 1, rate: 0, status: "中斷", filename: "interrupted.mp4" } });
    expect(store.byCategory.value.downloading.map((e) => e.filename)).not.toContain("interrupted.mp4");
  });

  it("中斷 live task does not appear in byCategory.waiting", () => {
    const store = useProgressStore();
    pushMessage({ "1": { sn: 1, rate: 0, status: "中斷", filename: "interrupted.mp4" } });
    expect(store.byCategory.value.waiting.map((e) => e.filename)).not.toContain("interrupted.mp4");
  });

  it("中斷 live task does not appear in byCategory.completed", () => {
    const store = useProgressStore();
    pushMessage({
      "1": {
        sn: 1,
        rate: 0,
        status: "中斷",
        filename: "interrupted.mp4",
        started_at: "2026-04-18T10:00:00Z",
        finished_at: "2026-04-18T11:00:00Z",
      },
    });
    expect(store.byCategory.value.completed.map((e) => e.filename)).not.toContain("interrupted.mp4");
  });

  it("totalCount is 0 when the only task is 中斷", () => {
    const store = useProgressStore();
    pushMessage({ "1": { sn: 1, rate: 0, status: "中斷", filename: "interrupted.mp4" } });
    expect(store.totalCount.value).toBe(0);
  });

  it("中斷 history entry does not appear in mergedCompleted", () => {
    const store = useProgressStore();

    store.historyEntries.value = [
      {
        id: 88,
        sn: 888,
        filename: "interrupted_hist.mp4",
        final_status: "中斷",
        retries: 0,
        finished_at: "2026-04-18T08:00:00+00:00",
      },
    ];

    const completed = store.byCategory.value.completed;
    expect(completed.map((e) => e.filename)).not.toContain("interrupted_hist.mp4");
  });
});

// ---------------------------------------------------------------------------
// Per-attempt history dedup — multiple attempts for same sn
// ---------------------------------------------------------------------------

describe("useProgressStore — per-attempt mergedCompleted", () => {
  beforeEach(() => {
    resetSocketStubs();
    __resetProgressStoreForTest();
  });

  afterEach(() => {
    vi.useRealTimers();
    __resetProgressStoreForTest();
  });

  it("test_merged_completed_shows_all_attempts_for_same_sn_within_7_days", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-19T12:00:00Z"));

    const store = useProgressStore();

    // Live completed entry for sn=100 with started_at=T3.
    pushMessage({
      "100": {
        sn: 100,
        rate: 100,
        status: "下載完成",
        filename: "ep_live.mp4",
        started_at: "2026-04-19T10:00:00Z",
        finished_at: "2026-04-19T11:00:00Z",
      },
    });

    // Two history rows for sn=100 with different started_at (earlier attempts).
    store.historyEntries.value = [
      {
        id: 1,
        sn: 100,
        filename: "ep_attempt1.mp4",
        final_status: "下載完成",
        retries: 1,
        started_at: "2026-04-17T08:00:00Z",
        finished_at: "2026-04-17T09:00:00Z",
      },
      {
        id: 2,
        sn: 100,
        filename: "ep_attempt2.mp4",
        final_status: "下載完成",
        retries: 0,
        started_at: "2026-04-18T08:00:00Z",
        finished_at: "2026-04-18T09:00:00Z",
      },
    ];

    const completed = store.byCategory.value.completed;
    // All three attempts must appear — live + 2 history rows.
    expect(completed.length).toBe(3);
    // Sorted newest first.
    expect(completed[0].filename).toBe("ep_live.mp4");
    expect(completed[1].filename).toBe("ep_attempt2.mp4");
    expect(completed[2].filename).toBe("ep_attempt1.mp4");
  });

  it("test_live_and_history_same_sn_different_started_at_collapse_to_one", () => {
    // Regression test: ProgressBus.force_finish (boot-time ghost
    // reconciliation for TG/BT) synthesises its live entry with
    // started_at=null, since the process that calls it never locally
    // start()ed the sn. The old "sn|started_at" dedup key never matched
    // the DB-history row's real started_at, so the same completed task
    // rendered as two MonitorView cards. A null-started_at live entry must
    // collapse against its DB-history counterpart by sn alone.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-19T12:00:00Z"));

    const store = useProgressStore();

    // Live ghost-reconciliation entry: started_at is null/absent.
    pushMessage({
      "555": {
        sn: 555,
        rate: 100,
        status: "下載完成",
        filename: "ghost_live.mp4",
        finished_at: "2026-04-19T11:00:00Z",
        // started_at intentionally omitted, mirroring force_finish's
        // synthesised entry.
      },
    });

    // DB-history row for the same sn with a real started_at (written by
    // record_start before the owning process died, and closed out by
    // LandingWorker/TgDownloadWatcher's own direct repo call).
    store.historyEntries.value = [
      {
        id: 6,
        sn: 555,
        filename: "hist_555.mp4",
        final_status: "下載完成",
        retries: 0,
        started_at: "2026-04-19T09:00:00Z",
        finished_at: "2026-04-19T11:00:00Z",
      },
    ];

    const completed = store.byCategory.value.completed;
    // Exactly one card for sn=555 — not two.
    expect(completed.filter((e) => e.sn === 555).length).toBe(1);
    // Live entry wins on collision (same precedence as the exact-match case).
    expect(completed.find((e) => e.sn === 555)?.filename).toBe("ghost_live.mp4");
  });

  it("test_two_entries_different_sn_both_render_as_separate_cards", () => {
    // Sanity guard against over-merging: distinct sn values must never
    // collapse into one card, even if one side has a null started_at.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-19T12:00:00Z"));

    const store = useProgressStore();

    pushMessage({
      "601": {
        sn: 601,
        rate: 100,
        status: "下載完成",
        filename: "ghost_601.mp4",
        finished_at: "2026-04-19T11:00:00Z",
      },
    });

    store.historyEntries.value = [
      {
        id: 7,
        sn: 602,
        filename: "hist_602.mp4",
        final_status: "下載完成",
        retries: 0,
        started_at: "2026-04-19T09:00:00Z",
        finished_at: "2026-04-19T10:00:00Z",
      },
    ];

    const completed = store.byCategory.value.completed;
    expect(completed.some((e) => e.sn === 601 && e.filename === "ghost_601.mp4")).toBe(true);
    expect(completed.some((e) => e.sn === 602 && e.filename === "hist_602.mp4")).toBe(true);
    expect(completed.length).toBe(2);
  });

  it("test_merged_completed_dedupes_live_and_history_same_attempt", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-19T12:00:00Z"));

    const store = useProgressStore();

    const sharedStartedAt = "2026-04-18T10:00:00Z";

    // Live completed entry for sn=100.
    pushMessage({
      "100": {
        sn: 100,
        rate: 100,
        status: "下載完成",
        filename: "ep_live.mp4",
        started_at: sharedStartedAt,
        finished_at: "2026-04-18T11:00:00Z",
      },
    });

    // History row for the same sn=100 with the identical started_at.
    store.historyEntries.value = [
      {
        id: 3,
        sn: 100,
        filename: "ep_hist.mp4",
        final_status: "下載完成",
        retries: 0,
        started_at: sharedStartedAt,
        finished_at: "2026-04-18T11:00:00Z",
      },
    ];

    const completed = store.byCategory.value.completed;
    // Same attempt — only one card; live entry wins.
    expect(completed.length).toBe(1);
    expect(completed[0].filename).toBe("ep_live.mp4");
  });
});

// ---------------------------------------------------------------------------
// mergedCompleted sort order
// ---------------------------------------------------------------------------

describe("useProgressStore — mergedCompleted sorted newest first", () => {
  beforeEach(() => {
    resetSocketStubs();
    __resetProgressStoreForTest();
  });

  afterEach(() => {
    vi.useRealTimers();
    __resetProgressStoreForTest();
  });

  it("test_mergedCompleted_sorted_newest_first: entries with different finished_at are ordered newest first", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));

    const store = useProgressStore();

    // Three live terminal entries with distinct finished_at timestamps.
    pushMessage({
      "10": {
        sn: 10,
        rate: 100,
        status: "下載完成",
        filename: "oldest.mp4",
        started_at: "2026-04-16T08:00:00Z",
        finished_at: "2026-04-16T09:00:00Z",
      },
      "20": {
        sn: 20,
        rate: 100,
        status: "下載完成",
        filename: "newest.mp4",
        started_at: "2026-04-18T10:00:00Z",
        finished_at: "2026-04-18T11:00:00Z",
      },
      "30": {
        sn: 30,
        rate: 100,
        status: "任務完成",
        filename: "middle.mp4",
        started_at: "2026-04-17T06:00:00Z",
        finished_at: "2026-04-17T07:00:00Z",
      },
    });

    const completed = store.byCategory.value.completed;
    expect(completed.length).toBe(3);
    // Index 0 must be the most recently finished entry.
    expect(completed[0].filename).toBe("newest.mp4");
    expect(completed[1].filename).toBe("middle.mp4");
    expect(completed[2].filename).toBe("oldest.mp4");
  });

  it("entries without finished_at sink to the bottom", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));

    const store = useProgressStore();

    // One entry with finished_at, one without (only started_at for the 7-day window check).
    pushMessage({
      "40": {
        sn: 40,
        rate: 100,
        status: "下載完成",
        filename: "no_finish.mp4",
        started_at: "2026-04-18T09:00:00Z",
        // no finished_at
      },
      "50": {
        sn: 50,
        rate: 100,
        status: "下載完成",
        filename: "has_finish.mp4",
        started_at: "2026-04-18T10:00:00Z",
        finished_at: "2026-04-18T11:00:00Z",
      },
    });

    const completed = store.byCategory.value.completed;
    expect(completed.length).toBe(2);
    // Entry with finished_at should be first.
    expect(completed[0].filename).toBe("has_finish.mp4");
    expect(completed[1].filename).toBe("no_finish.mp4");
  });

  it("merges history entries and sorts by finished_at desc across live + history", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-18T12:00:00Z"));

    const store = useProgressStore();

    // Live entry: finished later.
    pushMessage({
      "60": {
        sn: 60,
        rate: 100,
        status: "下載完成",
        filename: "live_newer.mp4",
        started_at: "2026-04-18T10:00:00Z",
        finished_at: "2026-04-18T11:30:00Z",
      },
    });

    // History entry: finished earlier, different sn.
    store.historyEntries.value = [
      {
        id: 5,
        sn: 70,
        filename: "hist_older.mp4",
        final_status: "任務完成",
        retries: 0,
        started_at: "2026-04-18T08:00:00Z",
        finished_at: "2026-04-18T09:00:00Z",
      },
    ];

    const completed = store.byCategory.value.completed;
    expect(completed.length).toBe(2);
    expect(completed[0].filename).toBe("live_newer.mp4");
    expect(completed[1].filename).toBe("hist_older.mp4");
  });
});
