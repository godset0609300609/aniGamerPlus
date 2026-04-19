/**
 * settings.spec.ts — Settings persistence flow.
 *
 * Flow:
 *  1. Visit /settings.
 *  2. Find the "下載冷卻時間（秒）" input-number and change the value to 30.
 *  3. Save via the DirtyFab → success toast "配置已成功提交".
 *  4. Reload the page.
 *  5. Verify the "下載冷卻時間（秒）" field now shows 30.
 */
import { expect, test } from '@playwright/test'

test.describe('settings persistence', () => {
  test('change download_cd to 30, save, reload, verify persisted', async ({ page }) => {
    await page.goto('/#/settings')
    await page.waitForSelector('.ag-brand', { timeout: 15_000 })

    // Wait for the form to be rendered (skeleton gone).
    await page.waitForSelector('form.el-form, .el-form', { timeout: 15_000 })

    // Find the "下載冷卻時間（秒）" label and its associated input-number.
    // Element Plus renders el-form-item labels as `.el-form-item__label`.
    // We locate the label then navigate to the sibling input-number.
    const cdFormItem = page
      .locator('.el-form-item')
      .filter({ has: page.locator('.el-form-item__label', { hasText: '下載冷卻時間（秒）' }) })
    const cdInput = cdFormItem.locator('input[type="number"], input.el-input__inner')
    await expect(cdInput).toBeVisible({ timeout: 10_000 })

    // Click the field, select all, type the new value.
    // el-input-number reacts to the native 'change' event (fired on blur).
    await cdInput.click()
    await page.keyboard.press('Control+a')
    await page.keyboard.type('30')
    await page.keyboard.press('Tab')
    // Wait for Vue reactivity to propagate and DirtyFab transition to complete.
    await page.waitForTimeout(400)

    // The DirtyFab should appear — it's a fixed floating bar rendered with a Vue Transition.
    // Wait for the transition to complete before clicking.
    const saveBtn = page.locator('.ag-fab').getByRole('button', { name: '儲存' })
    await expect(saveBtn).toBeVisible({ timeout: 8_000 })
    await saveBtn.click()

    // Success toast.
    await expect(page.locator('.el-message--success')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('.el-message--success')).toContainText('配置已成功提交')

    // Reload and verify the value persisted.
    await page.reload()
    await page.waitForSelector('.el-form', { timeout: 15_000 })

    const cdFormItem2 = page
      .locator('.el-form-item')
      .filter({ has: page.locator('.el-form-item__label', { hasText: '下載冷卻時間（秒）' }) })
    const cdInput2 = cdFormItem2.locator('input[type="number"], input.el-input__inner')
    await expect(cdInput2).toHaveValue('30', { timeout: 10_000 })
  })
})
