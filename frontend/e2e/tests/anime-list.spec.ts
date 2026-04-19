/**
 * anime-list.spec.ts — Anime list CRUD flow.
 *
 * Flow:
 *  1. Visit /anime-list.
 *  2. Click 新增項目 → a blank row appears in the 未分類 group.
 *  3. Set sn to a safe test value (99999).
 *  4. Save → success toast "追番清單已儲存".
 *  5. Verify the entry exists via GET /api/anime-list.
 *  6. Delete the entry.
 *  7. Save → success toast again.
 *  8. Verify the entry is gone.
 */
import { expect, test } from '@playwright/test'
import { E2E_API_PORT } from '../fixtures/server'

const API = `http://127.0.0.1:${E2E_API_PORT}`
const TEST_SN = 99999

test.describe('anime-list CRUD', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/#/anime-list')
    await page.waitForSelector('.ag-brand', { timeout: 15_000 })
    // Wait for the list to finish loading (skeleton gone).
    await page.waitForSelector('.ag-toolbar', { timeout: 15_000 })
  })

  test('add entry, save, verify, delete, save, verify', async ({ page }) => {
    // --- Add entry ---
    const addBtn = page.locator('button', { hasText: '新增項目' })
    await expect(addBtn).toBeVisible({ timeout: 10_000 })
    await addBtn.click()

    // The new blank row appears in the 未分類 collapse panel.
    // The sn input-number should be present (value 0).
    // We target the first sn input in the table.
    const snInput = page
      .locator('.ag-anime-table .ag-sn-input input')
      .first()
    await expect(snInput).toBeVisible({ timeout: 5_000 })

    // Set sn to TEST_SN.
    // fill() sets the input value and fires the `input` event which
    // el-input-number forwards to Vue v-model.
    await snInput.fill(String(TEST_SN))
    // Blur the input so el-input-number's change handler fires.
    await snInput.blur()
    // Wait for Vue dirty state to propagate.
    await page.waitForTimeout(300)

    // --- Save --- (DirtyFab shows a green 儲存 button when form is dirty)
    const dirtyFab = page.locator('.ag-fab')
    await expect(dirtyFab).toBeVisible({ timeout: 5_000 })
    const saveBtn = dirtyFab.getByRole('button', { name: '儲存' })
    await expect(saveBtn).toBeVisible({ timeout: 3_000 })
    await saveBtn.click()

    // Verify success toast and wait for the DirtyFab to disappear (confirming
    // save() + load() cycle completed and entries are back in sync).
    const successMsg = page.locator('.el-message--success')
    await expect(successMsg).toBeVisible({ timeout: 10_000 })
    await expect(successMsg).toContainText('追番清單已儲存')
    // DirtyFab disappears once save()+load() cycle finishes and dirty becomes false.
    await expect(page.locator('.ag-fab')).toBeHidden({ timeout: 10_000 })

    // --- Verify entry via API ---
    const listResp = await page.request.get(`${API}/api/anime-list`)
    expect(listResp.ok()).toBe(true)
    const listData = await listResp.json() as { entries: Array<{ sn: number }> }
    const found = listData.entries.some((e) => e.sn === TEST_SN)
    expect(found, `Expected sn=${TEST_SN} in entries`).toBe(true)

    // --- Delete entry ---
    // After save+reload the table is re-fetched.  The entry with TEST_SN
    // should be the only entry (fresh DB), so click the first 刪除 button.
    const firstDeleteBtn = page
      .locator('.ag-anime-table .el-table__row')
      .first()
      .locator('button', { hasText: '刪除' })
    await expect(firstDeleteBtn).toBeVisible({ timeout: 5_000 })
    await firstDeleteBtn.click()

    // --- Save again ---
    // Wait for DirtyFab to appear (the delete made the form dirty again).
    const dirtyFab2 = page.locator('.ag-fab')
    await expect(dirtyFab2).toBeVisible({ timeout: 5_000 })
    const saveBtn2 = dirtyFab2.getByRole('button', { name: '儲存' })
    await expect(saveBtn2).toBeVisible({ timeout: 3_000 })
    await saveBtn2.click()

    // Wait for the save to complete: a new success toast appears AND the DirtyFab
    // disappears again (dirty=false after save+load).
    await expect(page.locator('.el-message--success')).toBeVisible({ timeout: 10_000 })
    await expect(dirtyFab2).toBeHidden({ timeout: 10_000 })

    // --- Verify absence ---
    const listResp2 = await page.request.get(`${API}/api/anime-list`)
    expect(listResp2.ok()).toBe(true)
    const listData2 = await listResp2.json() as { entries: Array<{ sn: number }> }
    const absent = !listData2.entries.some((e) => e.sn === TEST_SN)
    expect(absent, `Expected sn=${TEST_SN} to be absent`).toBe(true)
  })
})
