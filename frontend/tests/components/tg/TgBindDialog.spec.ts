/**
 * Unit tests for TgBindDialog.vue — QR + phone-code Telegram bind flows.
 *
 * Focused on the phone flow's 'awaiting_code' retry UX (models.TgLoginStatus
 * gained this member alongside 'pending' | 'awaiting_password' | 'success' |
 * 'failed' so a wrong/expired code no longer 500s): a wrong or expired code
 * must keep the code input open with an inline error, not close the dialog
 * or bounce the user back to the phone-number step, and a subsequent
 * correct submission on the same login_token must still succeed.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createElementPlusStubs, elementPlusModuleMock } from '../../helpers/elementPlusStubs'
import type { TgLoginStatusResponse, TgPhoneLoginResponse } from '@/types'

const mockStartQrLogin = vi.fn()
const mockPollQrLogin = vi.fn()
const mockSubmitQrPassword = vi.fn()
const mockStartPhoneLogin = vi.fn()
const mockSubmitPhoneCode = vi.fn()
const mockSubmitPhonePassword = vi.fn()

vi.mock('@/api/tg', () => ({
  TgApi: vi.fn().mockImplementation(() => ({
    startQrLogin: mockStartQrLogin,
    pollQrLogin: mockPollQrLogin,
    submitQrPassword: mockSubmitQrPassword,
    startPhoneLogin: mockStartPhoneLogin,
    submitPhoneCode: mockSubmitPhoneCode,
    submitPhonePassword: mockSubmitPhonePassword,
  })),
}))

const isMobileRef = ref(false)

vi.mock('@/composables/useBreakpoint', () => ({
  useBreakpoint: () => ({
    isMobile: isMobileRef,
    isTablet: ref(false),
  }),
}))

const { mockElMessageSuccess } = vi.hoisted(() => ({
  mockElMessageSuccess: vi.fn(),
}))

vi.mock('element-plus', () =>
  elementPlusModuleMock({
    ElMessage: { success: mockElMessageSuccess, error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  }),
)

import TgBindDialog from '@/components/tg/TgBindDialog.vue'

const stubs = createElementPlusStubs()

function mountDialog() {
  return mount(TgBindDialog, {
    props: { modelValue: true },
    global: { stubs },
  })
}

async function switchToPhoneTab(wrapper: ReturnType<typeof mountDialog>): Promise<void> {
  const phoneTabItem = wrapper.find('.el-tabs__item[data-name="phone"]')
  expect(phoneTabItem.exists()).toBe(true)
  await phoneTabItem.trigger('click')
  await flushPromises()
}

async function goToCodeStep(wrapper: ReturnType<typeof mountDialog>, phone = '+886912345678'): Promise<void> {
  mockStartPhoneLogin.mockResolvedValue({ login_token: 'token-abc', phone } satisfies TgPhoneLoginResponse)
  await switchToPhoneTab(wrapper)

  const phoneInput = wrapper.find('input[placeholder="+886912345678"]')
  await phoneInput.setValue(phone)
  const sendBtn = wrapper.findAll('button').find((b) => b.text().includes('傳送驗證碼'))
  expect(sendBtn).toBeDefined()
  await sendBtn!.trigger('click')
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
  isMobileRef.value = false
  // Non-immediate watch on `modelValue` means startQrFlow is never actually
  // invoked when the dialog mounts already-open in these specs, but stub it
  // defensively in case that assumption ever changes.
  // B-10 (security audit): qr_code_url no longer round-trips in the response.
  mockStartQrLogin.mockResolvedValue({ login_token: 'qr-token', qr_code_png_base64: 'data:x' })
})

describe('TgBindDialog — phone flow, wrong/expired code retry (awaiting_code)', () => {
  it('shows the code input by default once a code has been sent', async () => {
    const wrapper = mountDialog()
    await goToCodeStep(wrapper)

    expect(wrapper.find('input[placeholder="請輸入 Telegram 傳送的驗證碼"]').exists()).toBe(true)
  })

  it('a wrong code (awaiting_code) shows an inline error and keeps the code input open', async () => {
    mockSubmitPhoneCode.mockResolvedValue({
      status: 'awaiting_code',
      error: '驗證碼錯誤，請重新輸入',
    } satisfies TgLoginStatusResponse)
    const wrapper = mountDialog()
    await goToCodeStep(wrapper)

    const codeInput = wrapper.find('input[placeholder="請輸入 Telegram 傳送的驗證碼"]')
    await codeInput.setValue('00000')
    const verifyBtn = wrapper.findAll('button').find((b) => b.text().trim() === '驗證')
    await verifyBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('驗證碼錯誤，請重新輸入')
    // Still on the code step — input stays open, not bounced back to the
    // phone-number step or closed.
    expect(wrapper.find('input[placeholder="請輸入 Telegram 傳送的驗證碼"]').exists()).toBe(true)
    expect(wrapper.find('input[placeholder="+886912345678"]').exists()).toBe(false)
    // Dialog must not close on a recoverable retry state.
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('an expired code (awaiting_code) shows its own distinct message', async () => {
    mockSubmitPhoneCode.mockResolvedValue({
      status: 'awaiting_code',
      error: '驗證碼已過期，請重新取得驗證碼',
    } satisfies TgLoginStatusResponse)
    const wrapper = mountDialog()
    await goToCodeStep(wrapper)

    const codeInput = wrapper.find('input[placeholder="請輸入 Telegram 傳送的驗證碼"]')
    await codeInput.setValue('00000')
    const verifyBtn = wrapper.findAll('button').find((b) => b.text().trim() === '驗證')
    await verifyBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('驗證碼已過期，請重新取得驗證碼')
  })

  it('a correct code after a prior wrong attempt still succeeds and closes the dialog', async () => {
    mockSubmitPhoneCode
      .mockResolvedValueOnce({ status: 'awaiting_code', error: '驗證碼錯誤，請重新輸入' } satisfies TgLoginStatusResponse)
      .mockResolvedValueOnce({ status: 'success', telegram_handle: 'retryuser' } satisfies TgLoginStatusResponse)
    const wrapper = mountDialog()
    await goToCodeStep(wrapper)

    const codeInput = wrapper.find('input[placeholder="請輸入 Telegram 傳送的驗證碼"]')
    const verifyBtn = wrapper.findAll('button').find((b) => b.text().trim() === '驗證')

    await codeInput.setValue('00000')
    await verifyBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('驗證碼錯誤，請重新輸入')

    await codeInput.setValue('11111')
    await verifyBtn!.trigger('click')
    await flushPromises()

    expect(mockSubmitPhoneCode).toHaveBeenCalledTimes(2)
    expect(mockElMessageSuccess).toHaveBeenCalled()
    expect(wrapper.emitted('bound')).toEqual([['retryuser']])
    expect(wrapper.emitted('update:modelValue')).toEqual([[false]])
  })

  it('switching to the 2FA password step (awaiting_password) still works alongside awaiting_code', async () => {
    mockSubmitPhoneCode.mockResolvedValue({ status: 'awaiting_password' } satisfies TgLoginStatusResponse)
    const wrapper = mountDialog()
    await goToCodeStep(wrapper)

    const verifyBtn = wrapper.findAll('button').find((b) => b.text().trim() === '驗證')
    await wrapper.find('input[placeholder="請輸入 Telegram 傳送的驗證碼"]').setValue('12345')
    await verifyBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('此帳號已啟用兩步驟驗證，請輸入密碼')
    expect(wrapper.find('input[placeholder="請輸入 Telegram 傳送的驗證碼"]').exists()).toBe(false)
  })
})
