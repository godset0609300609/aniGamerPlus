/**
 * Unit tests for BrowserExtensionDialog.vue — HTTPS-origin warning (MEDIUM-2
 * security audit fix) and the two-button userscript (📌 加入追番 +
 * ⬇️ 直接下載). The bookmarklet/userscript both bake the current
 * `window.location.origin` verbatim; over plain HTTP that means the
 * session cookie used by the popup they open travels unencrypted, so the
 * dialog must warn prominently when served over HTTP and stay quiet over
 * HTTPS.
 */
import { describe, expect, it, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createElementPlusStubs } from '../../helpers/elementPlusStubs'

import BrowserExtensionDialog from '@/components/animeList/BrowserExtensionDialog.vue'

const stubs = createElementPlusStubs()

function mountDialog() {
  return mount(BrowserExtensionDialog, {
    props: { modelValue: true },
    global: { stubs },
  })
}

function stubLocation(overrides: Partial<Location>): ReturnType<typeof vi.spyOn> {
  return vi
    .spyOn(window, 'location', 'get')
    .mockReturnValue({ origin: 'http://example.test', ...overrides } as Location)
}

describe('BrowserExtensionDialog — HTTPS warning', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows the HTTP warning when served over plain HTTP', () => {
    stubLocation({ protocol: 'http:' })

    const wrapper = mountDialog()

    expect(wrapper.text()).toContain('目前是 HTTP 環境')
    expect(wrapper.text()).toContain('中間人竊聽')
  })

  it('hides the HTTP warning when served over HTTPS', () => {
    stubLocation({ protocol: 'https:' })

    const wrapper = mountDialog()

    expect(wrapper.text()).not.toContain('目前是 HTTP 環境')
  })

  it('still renders the copy buttons when the HTTP warning is shown', () => {
    stubLocation({ protocol: 'http:' })

    const wrapper = mountDialog()

    const buttons = wrapper.findAll('button').filter((b) => b.text().includes('複製'))
    expect(buttons.length).toBeGreaterThan(0)
    for (const button of buttons) {
      expect(button.attributes('disabled')).toBeUndefined()
    }
  })
})

describe('BrowserExtensionDialog — two-button userscript', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('injects both quick-add and quick-download routes into the userscript', () => {
    stubLocation({ protocol: 'https:' })

    const wrapper = mountDialog()
    const codeBlocks = wrapper.findAll('pre.ag-code-block')
    const userscript = codeBlocks[0].text()

    expect(userscript).toContain("'quick-add'")
    expect(userscript).toContain("'quick-download'")
    expect(userscript).toContain('📌 加入追番')
    expect(userscript).toContain('⬇️ 直接下載')
  })

  it('substitutes the current origin into both button URLs in the userscript', () => {
    stubLocation({ protocol: 'https:', origin: 'https://anibutler.example' })

    const wrapper = mountDialog()
    const codeBlocks = wrapper.findAll('pre.ag-code-block')
    const userscript = codeBlocks[0].text()

    expect(userscript).toContain("'https://anibutler.example/#/' + path")
    expect(userscript).not.toContain('ORIGIN_PLACEHOLDER')
  })

  it('keeps the bookmarklet limited to the quick-add-only single action', () => {
    stubLocation({ protocol: 'https:', origin: 'https://anibutler.example' })

    const wrapper = mountDialog()
    const codeBlocks = wrapper.findAll('pre.ag-code-block')
    const bookmarklet = codeBlocks[1].text()

    expect(bookmarklet).toContain('https://anibutler.example/#/quick-add?sn=')
    expect(bookmarklet).not.toContain('quick-download')
    expect(wrapper.text()).toContain('書籤列版本僅支援')
  })

  it('mentions both floating buttons in the install-steps copy', () => {
    stubLocation({ protocol: 'https:' })

    const wrapper = mountDialog()

    expect(wrapper.text()).toContain('📌 加入追番」與「⬇️ 直接下載」兩個浮動按鈕')
  })
})
