/**
 * smoke.spec.ts — Basic page-load sanity checks.
 *
 * Verifies that:
 *  - The app shell loads and shows the "動畫管家" brand in the header.
 *  - The navigation contains exactly the three expected menu items.
 */
import { expect, test } from '@playwright/test'

test.describe('smoke', () => {
  test('app shell loads with correct header brand', async ({ page }) => {
    await page.goto('/')

    // The SPA uses hash-based routing; / redirects to /#/monitor.
    // The brand should always be visible once the shell renders.
    await expect(page.locator('.ag-brand')).toBeVisible({ timeout: 15_000 })
    await expect(page.locator('.ag-brand')).toContainText('動畫管家')
  })

  test('navigation has three items: 任務監控 / 追番清單 / 系統日誌', async ({ page }) => {
    await page.goto('/')
    await page.waitForSelector('.ag-menu', { timeout: 15_000 })

    const menuItems = page.locator('.ag-menu .el-menu-item')
    await expect(menuItems).toHaveCount(3)

    await expect(menuItems.nth(0)).toContainText('任務監控')
    await expect(menuItems.nth(1)).toContainText('追番清單')
    await expect(menuItems.nth(2)).toContainText('系統日誌')
  })
})
