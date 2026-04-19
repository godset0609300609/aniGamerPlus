/**
 * monitor.spec.ts — Monitor view checks.
 *
 * Verifies:
 *  - /monitor renders (no crash).
 *  - The FAB button for 新增手動任務 is present.
 *  - When there are no tasks, the empty state message "目前沒有任務" is shown.
 *
 * The "3 columns with 0 tasks" layout only appears when tasks exist; with an
 * empty DB the empty-state message is shown instead.  We assert the FAB is
 * always present regardless.
 */
import { expect, test } from '@playwright/test'

test.describe('monitor view', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/#/monitor')
    // Wait for app shell to be present (auth bypass means no login redirect).
    await page.waitForSelector('.ag-brand', { timeout: 15_000 })
  })

  test('FAB button for 新增手動任務 is visible', async ({ page }) => {
    // Wait for either the skeleton or the real content to be gone/shown.
    // The FAB is always rendered regardless of connection state.
    const fab = page.locator('.manual-task-fab')
    await expect(fab).toBeVisible({ timeout: 20_000 })
    await expect(fab).toHaveAttribute('title', '新增手動任務')
  })

  test('empty-state message is shown when no tasks exist', async ({ page }) => {
    // Fresh DB → no tasks → el-empty with description "目前沒有任務".
    // We wait up to 15 s for the WebSocket initial load to settle.
    // el-empty renders <div class="el-empty__description"><p>…</p></div>;
    // use the <p> specifically to avoid strict-mode multi-match.
    const emptyEl = page.locator('.el-empty__description p')
    await expect(emptyEl).toContainText('目前沒有任務', { timeout: 20_000 })
  })

  test('clicking the FAB opens the 新增手動任務 dialog', async ({ page }) => {
    const fab = page.locator('.manual-task-fab')
    await expect(fab).toBeVisible({ timeout: 15_000 })
    await fab.click()

    // The ManualTaskDialog should appear — look for the overlay dialog container.
    const dialog = page.locator('[role="dialog"][aria-label="手動新增任務"]')
    await expect(dialog).toBeVisible({ timeout: 5_000 })
  })
})
