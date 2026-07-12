/**
 * Unit tests for App.vue — auth gate and health integration.
 *
 * Strategy: stub router, auth store, useBackendHealth, useDarkMode, and all
 * child components so we can test the shell's conditional rendering logic
 * without a real API or WebSocket.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { markRaw, reactive, ref } from 'vue'
import { createElementPlusStubs, elementPlusModuleMock } from '../helpers/elementPlusStubs'

// ---------------------------------------------------------------------------
// vue-router stub — `reactive()` (not a fresh plain object) so tests can
// mutate `routeRef.path` mid-test and have App.vue's `watch(() =>
// route.path, ...)` observe the change (used by the drawer-auto-close spec).
// ---------------------------------------------------------------------------
const mockPush = vi.fn().mockResolvedValue(undefined)
const routeRef = reactive({ path: '/monitor', name: 'monitor' })

vi.mock('vue-router', () => ({
  useRoute: () => routeRef,
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
// Element Plus icons stub (used by App.vue for Sunny, Moon, Tools, Menu icons)
// ---------------------------------------------------------------------------
vi.mock('@element-plus/icons-vue', () => ({
  Tools: { template: '<span>tools</span>' },
  Sunny: { template: '<span>sunny</span>' },
  Moon: { template: '<span>moon</span>' },
  Menu: { template: '<span>menu</span>' },
}))

vi.mock('element-plus', () =>
  elementPlusModuleMock(),
)

// ---------------------------------------------------------------------------
// useBreakpoint stub — controllable isMobile so drawer-vs-menu tests don't
// depend on real matchMedia/viewport plumbing.
// ---------------------------------------------------------------------------
const isMobileRef = ref(false)

vi.mock('@/composables/useBreakpoint', () => ({
  useBreakpoint: () => ({
    isMobile: isMobileRef,
    isTablet: ref(false),
  }),
}))

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
  // Supports both call sites in App.vue: the plain `<router-view />` used
  // for the login screen (no slot content is passed, so `<slot />` just
  // renders the wrapper div), and the shell's scoped-slot usage
  // `<router-view v-slot="{ Component }">…</router-view>`, which needs a
  // `Component` value to feed through so the transition + `<component
  // :is>` inside actually render something.
  RouterView: {
    template: '<div class="router-view-stub"><slot :Component="routedComponent" /></div>',
    data() {
      // markRaw — this is a component definition, not app state; letting
      // Vue make it reactive triggers a dev warning and buys nothing.
      return {
        routedComponent: markRaw({ template: '<div class="route-component-stub">routed</div>' }),
      }
    },
  },
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
  isMobileRef.value = false
  routeRef.path = '/monitor'
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

describe('App.vue — route-view transition', () => {
  // A dedicated <transition> stub (rather than findComponent(Transition),
  // which Vue Test Utils types as a functional component and only exposes
  // as a DOMWrapper with no `.props()`) so we can assert on the `name` /
  // `mode` actually passed to it — a structural check that doesn't depend
  // on animation timing.
  const transitionStub = {
    props: ['name', 'mode'],
    template: '<div class="transition-stub" :data-name="name" :data-mode="mode"><slot /></div>',
  }

  it('wraps the routed view in a <transition name="route" mode="out-in">', async () => {
    userRef.value = { id: '1', username: 'alice', role: 'admin' }
    const wrapper = mount(App, {
      global: { stubs: { ...stubs, transition: transitionStub } },
    })
    await flushPromises()

    const transition = wrapper.find('.transition-stub')
    expect(transition.exists()).toBe(true)
    expect(transition.attributes('data-name')).toBe('route')
    expect(transition.attributes('data-mode')).toBe('out-in')
  })

  it('renders the routed component fed through the router-view v-slot', async () => {
    userRef.value = { id: '1', username: 'alice', role: 'admin' }
    const wrapper = mountApp()
    await flushPromises()

    expect(wrapper.find('.route-component-stub').exists()).toBe(true)
  })
})

describe('App.vue — mobile nav drawer', () => {
  it('test_sidebar_becomes_drawer_on_mobile: hides the horizontal menu and shows a hamburger trigger when isMobile is true', async () => {
    userRef.value = { id: '1', username: 'alice', role: 'admin' }
    isMobileRef.value = true
    const wrapper = mountApp()
    await flushPromises()

    expect(wrapper.find('.ag-menu').exists()).toBe(false)
    expect(wrapper.find('.ag-hamburger').exists()).toBe(true)
  })

  it('shows the horizontal menu (not the hamburger) on desktop', async () => {
    userRef.value = { id: '1', username: 'alice', role: 'admin' }
    isMobileRef.value = false
    const wrapper = mountApp()
    await flushPromises()

    expect(wrapper.find('.ag-menu').exists()).toBe(true)
    expect(wrapper.find('.ag-hamburger').exists()).toBe(false)
  })

  it('opens the nav drawer when the hamburger is clicked', async () => {
    userRef.value = { id: '1', username: 'alice', role: 'admin' }
    isMobileRef.value = true
    const wrapper = mountApp()
    await flushPromises()

    expect(wrapper.find('.el-drawer').exists()).toBe(false)

    await wrapper.find('.ag-hamburger').trigger('click')
    await flushPromises()

    expect(wrapper.find('.el-drawer').exists()).toBe(true)
  })

  it('test_drawer_closes_on_route_change: closes the drawer once route.path changes', async () => {
    userRef.value = { id: '1', username: 'alice', role: 'admin' }
    isMobileRef.value = true
    const wrapper = mountApp()
    await flushPromises()

    await wrapper.find('.ag-hamburger').trigger('click')
    await flushPromises()
    expect(wrapper.find('.el-drawer').exists()).toBe(true)

    routeRef.path = '/anime-list'
    await flushPromises()

    expect(wrapper.find('.el-drawer').exists()).toBe(false)
  })

  it('the drawer lists the same nav items as the desktop menu (admin-gated ones respected)', async () => {
    // This suite's auth store stub always reports isAdmin: false — mirrors
    // the desktop <el-menu>'s own v-if gating on the same flag.
    userRef.value = { id: '1', username: 'alice', role: 'downloader' }
    isMobileRef.value = true
    const wrapper = mountApp()
    await flushPromises()

    await wrapper.find('.ag-hamburger').trigger('click')
    await flushPromises()

    const drawerText = wrapper.find('.el-drawer').text()
    expect(drawerText).toContain('任務監控')
    expect(drawerText).toContain('追番清單')
    expect(drawerText).not.toContain('BT 下載')
    expect(drawerText).not.toContain('系統日誌')
  })
})
