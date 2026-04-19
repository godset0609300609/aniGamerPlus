/**
 * cookie.spec.ts — Cookie configuration flow.
 *
 * Flow:
 *  1. Visit /settings.
 *  2. Scroll to the Cookie section.
 *  3. Verify the status tag says "尚未設定".
 *  4. Enter a dummy cookie string in the password input.
 *  5. Click 儲存 → success toast "Cookie 已更新".
 *  6. Verify the status tag updates to "目前已設定".
 */
import { expect, test } from '@playwright/test'

const DUMMY_COOKIE = 'e2e_test_cookie_value=abcdef12345; path=/; domain=.example.com'

// Run cookie tests serially so the "initially 尚未設定" check runs before the
// "set cookie" test writes to the shared backend workspace.
test.describe.serial('cookie configuration', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/#/settings')
    await page.waitForSelector('.ag-brand', { timeout: 15_000 })
    // Wait for form to load.
    await page.waitForSelector('.el-form', { timeout: 15_000 })
  })

  test('cookie status tag initially shows 尚未設定', async ({ page }) => {
    const cookieSection = page
      .locator('section.ag-section')
      .filter({ has: page.locator('h2.ag-section-title', { hasText: 'Cookie' }) })
    const statusTag = cookieSection.locator('.el-tag')
    await expect(statusTag).toBeVisible({ timeout: 10_000 })
    await expect(statusTag).toContainText('尚未設定')
  })

  test('set cookie → tag flips to 目前已設定', async ({ page }) => {
    const cookieSection = page
      .locator('section.ag-section')
      .filter({ has: page.locator('h2.ag-section-title', { hasText: 'Cookie' }) })

    const statusTag = cookieSection.locator('.el-tag')
    await expect(statusTag).toContainText('尚未設定', { timeout: 10_000 })

    // Enter cookie in the password input.
    const cookieInput = cookieSection.locator('.cookie-row input[type="password"], .cookie-row input')
    await cookieInput.fill(DUMMY_COOKIE)

    // Click the 儲存 button inside the cookie row.
    const cookieSaveBtn = cookieSection.locator('.cookie-row button', { hasText: '儲存' })
    await expect(cookieSaveBtn).toBeEnabled({ timeout: 5_000 })
    await cookieSaveBtn.click()

    // Success toast.
    await expect(page.locator('.el-message--success')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('.el-message--success')).toContainText('Cookie 已更新')

    // Status tag should flip.
    await expect(statusTag).toContainText('目前已設定', { timeout: 5_000 })
  })
})
