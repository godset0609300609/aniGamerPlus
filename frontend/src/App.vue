<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Menu, Tools, Sunny, Moon } from '@element-plus/icons-vue'
import UserMenu from './components/UserMenu.vue'
import HeaderTaskIndicator from './components/HeaderTaskIndicator.vue'
import BackendOfflineOverlay from './components/BackendOfflineOverlay.vue'
import { useBreakpoint } from './composables/useBreakpoint'
import { useDarkMode } from './composables/useDarkMode'
import { useBackendHealth } from './composables/useBackendHealth'
import { useAuthStore } from './stores/auth'

const route = useRoute()
const router = useRouter()
const activeIndex = computed(() => route.path)

const { isDark, toggle } = useDarkMode()
const { isMobile } = useBreakpoint()
const { user, loading, isAdmin, loadMe } = useAuthStore()
const health = useBackendHealth()

// ---------------------------------------------------------------------------
// Nav items — shared between the desktop horizontal <el-menu> and the
// mobile drawer's vertical one so the two never drift out of sync.
// ---------------------------------------------------------------------------
interface NavItem {
  index: string
  label: string
  adminOnly?: boolean
}

const NAV_ITEMS: NavItem[] = [
  { index: '/monitor', label: '任務監控' },
  { index: '/anime-list', label: '追番清單' },
  { index: '/bt', label: 'BT 下載', adminOnly: true },
  { index: '/tg', label: 'Telegram 下載' },
  { index: '/logs', label: '系統日誌', adminOnly: true },
]

const visibleNavItems = computed(() =>
  NAV_ITEMS.filter((item) => !item.adminOnly || isAdmin.value),
)

// ---------------------------------------------------------------------------
// Mobile nav drawer — hamburger-triggered, auto-closes on route change.
// ---------------------------------------------------------------------------
const drawerOpen = ref(false)

watch(
  () => route.path,
  () => {
    drawerOpen.value = false
  },
)

onMounted(async () => {
  health.start()
  await loadMe()
  // If unauthenticated and auth is active (user remains null after load),
  // navigate to /login so the router-view renders LoginView.
  if (user.value === null && route.name !== 'login') {
    void router.push({ name: 'login' })
  }
})

onUnmounted(() => {
  health.stop()
})

/**
 * Auth-enabled is signalled by the backend: if /api/auth/me returns 401
 * after loading, user is null and we show LoginView via router.
 *
 * We only gate the shell AFTER loading is complete to avoid flicker.
 */
const showLogin = computed(() => !loading.value && user.value === null)
const showShell = computed(() => !loading.value && user.value !== null)
</script>

<template>
  <!-- Not authenticated (after load): show login page via router-view. -->
  <router-view v-if="showLogin" />

  <!-- Authenticated: normal dashboard shell. -->
  <el-container
    v-else-if="showShell"
    direction="vertical"
    class="ag-shell"
  >
    <!-- Backend offline overlay — rendered via Teleport to <body> -->
    <BackendOfflineOverlay
      v-if="health.state.value === 'offline'"
      :retry-count="health.retryCount.value"
      @retry="health.ping()"
    />

    <!-- Degraded banner — scheduler unreachable but API is up.
         Rendered in a fixed-position slot so it overlays the page without
         shifting layout or causing a whole-page scrollbar. -->
    <div
      v-else-if="health.state.value === 'degraded'"
      class="ag-degraded-banner-slot"
    >
      <el-alert
        title="排程服務暫時無回應，手動任務可能延遲"
        type="warning"
        :closable="false"
        class="ag-degraded-banner"
      />
    </div>

    <el-header class="ag-header">
      <!-- Mobile: hamburger opens the nav drawer instead of the horizontal menu -->
      <el-button
        v-if="isMobile"
        circle
        class="ag-hamburger"
        title="選單"
        @click="drawerOpen = true"
      >
        <el-icon :size="20">
          <Menu />
        </el-icon>
      </el-button>

      <div class="ag-brand">
        影片管家
      </div>

      <el-menu
        v-if="!isMobile"
        mode="horizontal"
        :router="true"
        :default-active="activeIndex"
        :ellipsis="false"
        class="ag-menu"
      >
        <el-menu-item
          v-for="item in visibleNavItems"
          :key="item.index"
          :index="item.index"
        >
          {{ item.label }}
        </el-menu-item>
      </el-menu>

      <div class="ag-actions">
        <el-button
          circle
          title="自動模式設定"
          :class="{ 'ag-gear-active': activeIndex === '/settings' }"
          @click="router.push('/settings')"
        >
          <el-icon :size="18">
            <Tools />
          </el-icon>
        </el-button>
        <el-button
          circle
          :title="isDark ? '切換為淺色模式' : '切換為深色模式'"
          @click="toggle"
        >
          <el-icon :size="18">
            <Sunny v-if="isDark" />
            <Moon v-else />
          </el-icon>
        </el-button>
        <HeaderTaskIndicator />
        <div class="ag-divider" />
        <UserMenu />
      </div>
    </el-header>

    <!-- Mobile nav drawer — mirrors the desktop horizontal menu's items. -->
    <el-drawer
      v-model="drawerOpen"
      direction="ltr"
      size="240px"
      :with-header="false"
      class="ag-nav-drawer"
    >
      <el-menu
        :router="true"
        :default-active="activeIndex"
        class="ag-drawer-menu"
      >
        <el-menu-item
          v-for="item in visibleNavItems"
          :key="item.index"
          :index="item.index"
        >
          {{ item.label }}
        </el-menu-item>
      </el-menu>
    </el-drawer>

    <el-main>
      <router-view v-slot="{ Component }">
        <transition
          name="route"
          mode="out-in"
        >
          <component :is="Component" />
        </transition>
      </router-view>
    </el-main>
  </el-container>
</template>

<style scoped>
.ag-shell {
  min-height: 100vh;
}
.ag-header {
  display: flex;
  align-items: center;
  gap: 16px;
  background-color: #212529;
  color: white;
  padding: 0 20px;
  position: sticky;
  top: 0;
  z-index: 100;
}
.ag-brand {
  font-weight: 700;
  font-size: 18px;
  white-space: nowrap;
}
.ag-menu {
  flex: 1;
  --el-menu-bg-color: transparent;
  --el-menu-text-color: #e9ecef;
  --el-menu-hover-bg-color: rgba(255, 255, 255, 0.08);
  --el-menu-active-color: #4caf50;
  border-bottom: none;
}
.ag-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.ag-actions :deep(.el-button + .el-button),
.ag-actions :deep(.el-dropdown) {
  margin-left: 0;
}
.ag-divider {
  flex-shrink: 0;
  width: 1px;
  height: 28px;
  background-color: rgba(255, 255, 255, 0.4);
  margin: 0 10px;
}
.ag-gear-active {
  color: #4caf50 !important;
  border-color: #4caf50 !important;
}

/* Hamburger trigger (mobile only) — explicit 44px box for a comfortable
   tap target, matching the touch-target minimum used across the app. */
.ag-hamburger {
  flex-shrink: 0;
  width: 44px;
  height: 44px;
  padding: 0;
}

/* Nav drawer — plain vertical menu, no dark-header color overrides (the
   drawer surface follows the page's light/dark background, not the
   header's fixed dark bar). Body padding removed so menu items reach
   the drawer's edges like a native nav list. */
.ag-drawer-menu {
  border-right: none;
}
.ag-nav-drawer :deep(.el-drawer__body) {
  padding: 0;
}

@media (max-width: 767px) {
  .ag-header {
    padding: 0 12px;
    gap: 8px;
  }
  .ag-brand {
    flex: 1;
    font-size: 16px;
  }
  .ag-actions {
    gap: 4px;
  }
}
/* Degraded banner overlay — sits above all content without shifting layout */
.ag-degraded-banner-slot {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 1000;
  /* Transparent to pointer events on the slot itself; the alert inside is
     still interactive so a future close button would work. */
  pointer-events: none;
}
.ag-degraded-banner {
  border-radius: 0;
  pointer-events: auto;
}

/* Route-view transition — fade + slight upward slide.
   `mode="out-in"` lets the old view leave fully before the new one enters,
   which also masks the brief blank moment while a lazy-loaded route
   component resolves. */
.route-enter-active,
.route-leave-active {
  transition: opacity 180ms cubic-bezier(0.4, 0, 0.2, 1),
    transform 180ms cubic-bezier(0.4, 0, 0.2, 1);
}
.route-enter-from {
  opacity: 0;
  transform: translateY(6px);
}
.route-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}
</style>
