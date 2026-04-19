import { describe, expect, it, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import BackendOfflineOverlay from '@/components/BackendOfflineOverlay.vue'

// The overlay uses <Teleport to="body">. Vue Test Utils renders teleports
// into the teleport target (document.body), so we query via document.body.
function mountOverlay(props: { retryCount?: number } = {}) {
  return mount(BackendOfflineOverlay, {
    props,
    attachTo: document.body,
    global: {
      // Stub Transition so it renders slot immediately without animation delays.
      stubs: { Transition: { template: '<slot />' } },
    },
  })
}

function bodyText(): string {
  return document.body.textContent ?? ''
}

function bodyFind(selector: string): Element | null {
  return document.body.querySelector(selector)
}

afterEach(() => {
  // Clean up any remaining overlay elements from document.body.
  document.body.querySelectorAll('.ag-offline-overlay').forEach((el) => el.remove())
})

// ---------------------------------------------------------------------------
// Basic rendering
// ---------------------------------------------------------------------------
describe('BackendOfflineOverlay — basic rendering', () => {
  it('renders the offline overlay box', () => {
    const wrapper = mountOverlay()
    expect(bodyFind('.ag-offline-overlay')).not.toBeNull()
    wrapper.unmount()
  })

  it('shows the 後端服務異常 message', () => {
    const wrapper = mountOverlay()
    expect(bodyText()).toContain('後端服務異常，嘗試重新連線…')
    wrapper.unmount()
  })

  it('shows the 手動重試 button', () => {
    const wrapper = mountOverlay()
    expect(bodyFind('.ag-offline-retry-btn')).not.toBeNull()
    wrapper.unmount()
  })
})

// ---------------------------------------------------------------------------
// Retry count
// ---------------------------------------------------------------------------
describe('BackendOfflineOverlay — retry count', () => {
  it('shows attempt count when retryCount > 0', () => {
    const wrapper = mountOverlay({ retryCount: 3 })
    expect(bodyText()).toContain('嘗試第 3 次')
    wrapper.unmount()
  })

  it('hides attempt paragraph when retryCount is 0', () => {
    const wrapper = mountOverlay({ retryCount: 0 })
    expect(bodyFind('.ag-offline-attempts')).toBeNull()
    wrapper.unmount()
  })

  it('hides attempt paragraph when retryCount is undefined', () => {
    const wrapper = mountOverlay()
    expect(bodyFind('.ag-offline-attempts')).toBeNull()
    wrapper.unmount()
  })
})

// ---------------------------------------------------------------------------
// Retry emit
// ---------------------------------------------------------------------------
describe('BackendOfflineOverlay — retry emit', () => {
  it('emits retry when 手動重試 button is clicked', async () => {
    const wrapper = mountOverlay({ retryCount: 2 })
    const btn = bodyFind('.ag-offline-retry-btn') as HTMLElement
    expect(btn).not.toBeNull()
    btn.click()
    await Promise.resolve()
    expect(wrapper.emitted('retry')).toHaveLength(1)
    wrapper.unmount()
  })
})
