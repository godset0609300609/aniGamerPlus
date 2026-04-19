import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import zhTw from 'element-plus/es/locale/lang/zh-tw'

import App from './App.vue'
import { router } from './router'
import './style.css'

const app = createApp(App)
app.use(router)
app.use(ElementPlus, { locale: zhTw })
app.mount('#app')
