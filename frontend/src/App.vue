<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Tools, Sunny, Moon } from '@element-plus/icons-vue'
import UserMenu from './components/UserMenu.vue'
import HeaderTaskIndicator from './components/HeaderTaskIndicator.vue'
import BackendOfflineOverlay from './components/BackendOfflineOverlay.vue'
import { useDarkMode } from './composables/useDarkMode'
import { useBackendHealth } from './composables/useBackendHealth'
import { useAuthStore } from './stores/auth'

const route = useRoute()
const router = useRouter()
const activeIndex = computed(() => route.path)

const { isDark, toggle } = useDarkMode()
const { user, loading, loadMe } = useAuthStore()
const health = useBackendHealth()

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

    <!-- Degraded banner — scheduler unreachable but API is up -->
    <el-alert
      v-else-if="health.state.value === 'degraded'"
      title="排程服務暫時無回應，手動任務可能延遲"
      type="warning"
      :closable="false"
      class="ag-degraded-banner"
    />

    <el-header class="ag-header">
      <div class="ag-brand">
        動畫管家
      </div>
      <el-menu
        mode="horizontal"
        :router="true"
        :default-active="activeIndex"
        :ellipsis="false"
        class="ag-menu"
      >
        <el-menu-item index="/monitor">
          任務監控
        </el-menu-item>
        <el-menu-item index="/anime-list">
          追番清單
        </el-menu-item>
        <el-menu-item index="/logs">
          系統日誌
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

    <el-main>
      <router-view />
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
.ag-degraded-banner {
  border-radius: 0;
}
</style>
