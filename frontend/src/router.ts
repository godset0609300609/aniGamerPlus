import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from './stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    public?: boolean
    requiresAdmin?: boolean
  }
}

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('./views/LoginView.vue'),
    meta: { title: '登入', public: true },
  },
  {
    path: '/',
    redirect: '/monitor',
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('./views/SettingsView.vue'),
    meta: { title: '自動模式設定' },
  },
  {
    path: '/monitor',
    name: 'monitor',
    component: () => import('./views/MonitorView.vue'),
    meta: { title: '任務監控' },
  },
  {
    path: '/anime-list',
    name: 'anime-list',
    component: () => import('./views/AnimeListView.vue'),
    meta: { title: '追番清單' },
  },
  {
    path: '/logs',
    name: 'logs',
    component: () => import('./views/LogsView.vue'),
    meta: { title: '系統日誌', requiresAdmin: true },
  },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

/**
 * Navigation guard:
 * - If the user is not logged in AND auth is enabled, redirect to /login.
 * - The /login route itself is always accessible (meta.public = true).
 * - We only redirect if loading has finished (loadMe is called in App.vue
 *   on mount; by the time the guard fires after the first navigation, user
 *   state should be resolved).  For subsequent navigations loading is false.
 */
router.beforeEach((to) => {
  if (to.meta.public) return true

  const { user, loading, isAdmin } = useAuthStore()

  // While loading we don't block — App.vue controls the visible content.
  if (loading.value) return true

  // If auth is disabled (user is populated as anon), let through.
  if (user.value === null) return { name: 'login' }

  // Admin-only routes: redirect non-admin users to /monitor.
  if (to.meta.requiresAdmin && !isAdmin.value) return { name: 'monitor' }

  return true
})
