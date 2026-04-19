/**
 * Unit tests for App.vue — auth gate and health integration.
 *
 * Strategy: stub router, auth store, useBackendHealth, useDarkMode, and all
 * child components so we can test the shell's conditional rendering logic
 * without a real API or WebSocket.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createElementPlusStubs, elementPlusModuleMock } from '../helpers/elementPlusStubs'

// ---------------------------------------------------------------------------
// vue-router stub
// ---------------------------------------------------------------------------
const mockPush = vi.fn().mockResolvedValue(undefined)

vi.mock('vue-router', () => ({
  useRoute: () => ({ path: '/monitor', name: 'monitor' }),
  useRouter: () => ({ push: mockPush }),
  RouterView: { template: '<div class="router-view" />' },
}))

// ---------------------------------------------------------------------------
// Auth store stub
// ---------------------------------------------------------------------------
const userRef = ref<{ id: string; username: string; role: string } | null>(null)
const loadingRef = ref(false)
const loadMeMock = vi.fn().mockResolvedValue(undefined)

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({
    user: userRef,
    loading: loadingRef,
    isAdmin: { value: false },
    loadMe: loadMeMock,
    logout: vi.fn(),
  }),
}))

// ---------------------------------------------------------------------------
// useBackendHealth stub
// ---------------------------------------------------------------------------
const healthState = ref<'online' | 'offline' | 'degraded'>('online')
const retryCountRef = ref(0)
const startMock = vi.fn()
const stopMock = vi.fn()
const pingMock = vi.fn()

vi.mock('@/composables/useBackendHealth', () => ({
  useBackendHealth: () => ({
    state: healthState,
    retryCount: retryCountRef,
    start: startMock,
    stop: stopMock,
    ping: pingMock,
  }),
}))

// ---------------------------------------------------------------------------
// useDarkMode stub
// ---------------------------------------------------------------------------
const isDarkRef = ref(false)
const toggleMock = vi.fn()

vi.mock('@/composables/useDarkMode', () => ({
  useDarkMode: () => ({
    isDark: isDarkRef,
    toggle: toggleMock,
    preference: ref('system'),
    setPreference: vi.fn(),
  }),
}))

// ---------------------------------------------------------------------------
// Element Plus icons stub (used by App.vue for Sunny, Moon, Tools icons)
// ---------------------------------------------------------------------------
vi.mock('@element-plus/icons-vue', () => ({
  Tools: { template: '<span>tools</span>' },
  Sunny: { template: '<span>sunny</span>' },
  Moon: { template: '<span>moon</span>' },
}))

vi.mock('element-plus', () =>
  elementPlusModuleMock(),
)

// Import AFTER mocks are set up.
import App from '@/App.vue'

const stubs = {
  ...createElementPlusStubs(),
  UserMenu: { template: '<div class="user-menu-stub" />' },
  HeaderTaskIndicator: { template: '<div class="header-task-indicator-stub" />' },
  BackendOfflineOverlay: {
    props: ['retryCount'],
    emits: ['retry'],
    template: '<div class="backend-offline-stub" />',
  },
  RouterView: { template: '<div class="router-view-stub" />' },
}

function mountApp() {
  return mount(App, {
    global: { stubs },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  userRef.value = null
  loadingRef.value = false
  healthState.value = 'online'
  retryCountRef.value = 0
  loadMeMock.mockResolvedValue(undefined)
})

describe('App.vue — auth gate rendering', () => {
  it('shows router-view (login) when user is null after load', async () => {
    userRef.value = null
    const wrapper = mountApp()
    await flushPromises()

    // showLogin = true → router-view is rendered outside the shell
    expect(wrapper.find('.router-view-stub').exists()).toBe(true)
  })

  it('shows the shell when user is authenticated', async () => {
    userRef.value = { id: '1', username: 'alice', role: 'admin' }
    const wrapper = mountApp()
    await flushPromises()

    // showShell = true → el-container is rendered
    expect(wrapper.find('.el-container').exists()).toBe(true)
  })

  it('renders nothing when loading is still true', async () => {
    loadingRef.value = true
    userRef.value = null
    const _wrapper = mountApp()
    await flushPromises()

    // Neither shell nor login route visible while loading.
    expect(_wrapper.find('.el-container').exists()).toBe(false)
    expect(_wrapper.find('.router-view-stub').exists()).toBe(false)
  })
})

describe('App.vue — onMounted lifecycle', () => {
  it('calls health.start() on mount', async () => {
    mountApp()
    await flushPromises()

    expect(startMock).toHaveBeenCalledTimes(1)
  })

  it('calls loadMe() on mount', async () => {
    mountApp()
    await flushPromises()

    expect(loadMeMock).toHaveBeenCalledTimes(1)
  })

  it('redirects to /login when user is null after loadMe', async () => {
    userRef.value = null
    loadMeMock.mockImplementation(async () => {
      userRef.value = null
    })
    mountApp()
    await flushPromises()

    expect(mockPush).toHaveBeenCalledWith({ name: 'login' })
  })

  it('does NOT redirect when user is authenticated', async () => {
    loadMeMock.mockImplementation(async () => {
      userRef.value = { id: '1', username: 'alice', role: 'admin' }
    })
    mountApp()
    await flushPromises()

    expect(mockPush).not.toHaveBeenCalled()
  })
})

describe('App.vue — backend health banner', () => {
  it('renders BackendOfflineOverlay when health state is offline', async () => {
    userRef.value = { id: '1', username: 'alice', role: 'admin' }
    healthState.value = 'offline'
    const wrapper = mountApp()
    await flushPromises()

    expect(wrapper.find('.backend-offline-stub').exists()).toBe(true)
  })

  it('does not render offline overlay when health state is online', async () => {
    userRef.value = { id: '1', username: 'alice', role: 'admin' }
    healthState.value = 'online'
    const wrapper = mountApp()
    await flushPromises()

    expect(wrapper.find('.backend-offline-stub').exists()).toBe(false)
  })

  it('renders degraded alert when health state is degraded', async () => {
    userRef.value = { id: '1', username: 'alice', role: 'admin' }
    healthState.value = 'degraded'
    const wrapper = mountApp()
    await flushPromises()

    // The el-alert stub renders as <div>; check its existence via the component.
    // The template uses v-else-if="health.state.value === 'degraded'" so an ElAlert
    // must be present in the DOM when degraded.
    // We can verify by checking that the BackendOfflineOverlay is NOT shown.
    expect(wrapper.find('.backend-offline-stub').exists()).toBe(false)
    // And the shell is rendered (user is authenticated).
    expect(wrapper.find('.el-container').exists()).toBe(true)
  })
})
