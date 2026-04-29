import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import zhTw from 'element-plus/es/locale/lang/zh-tw'

import App from './App.vue'
import { router } from './router'
import './style.css'
import {
  applyTelegramTheme,
  getTelegramWebApp,
  isTelegramWebAppLaunch,
  loginViaTelegramWebApp,
} from './api/telegram_webapp'

async function bootstrap() {
  if (isTelegramWebAppLaunch()) {
    const wa = getTelegramWebApp()
    wa?.ready()
    wa?.expand()
    applyTelegramTheme()
    try {
      await loginViaTelegramWebApp()
    } catch (err) {
      console.warn('Telegram WebApp auto-login failed; falling back:', err)
    }
  }

  const app = createApp(App)
  app.use(router)
  app.use(ElementPlus, { locale: zhTw })
  app.mount('#app')
}

void bootstrap()
