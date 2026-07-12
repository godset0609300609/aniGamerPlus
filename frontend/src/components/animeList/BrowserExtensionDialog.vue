<script setup lang="ts">
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { DocumentCopy } from '@element-plus/icons-vue'
import { useBreakpoint } from '@/composables/useBreakpoint'

/**
 * BrowserExtensionDialog — explains and hands out the two client-side
 * "quick add" integrations for 動畫瘋 anime pages:
 *
 *  - a Tampermonkey userscript (recommended) that injects a floating
 *    "📌 加入追番" button
 *  - a drag-to-bookmarks-bar bookmarklet as an install-free fallback
 *
 * Both snippets open a popup at `${origin}/#/quick-add?sn=...&title=...`,
 * which is same-origin with this app and therefore reuses the existing
 * session cookie — no cross-origin API call or CORS configuration needed.
 */

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

const { isMobile } = useBreakpoint()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

// Resolved lazily (not at module load) so the same bundle works whether the
// app is served from the production domain or a local dev server.
const origin = computed(() =>
  typeof window !== 'undefined' && window.location ? window.location.origin : '',
)

// MEDIUM-2 (security audit): both snippets below bake the *current* origin
// in verbatim — if that origin is http://, the popup they open (and with
// it the session cookie that authenticates it) travels over plain HTTP,
// which is trivially interceptable on a shared/untrusted network. Warn
// prominently rather than block: a developer running the stack locally
// over HTTP is a legitimate use case, they just need to know.
const isInsecureOrigin = computed(
  () =>
    typeof window !== 'undefined' &&
    !!window.location &&
    window.location.protocol !== 'https:',
)

const USERSCRIPT_TEMPLATE = `// ==UserScript==
// @name         aniGamerPlus 快速加入追番
// @namespace    https://anibutler.example
// @match        https://ani.gamer.com.tw/animeVideo.php*
// @grant        none
// @run-at       document-idle
// @description  在動畫瘋作品頁注入按鈕，一鍵加入 aniGamerPlus 追番清單
// ==/UserScript==
(function () {
  'use strict';
  var m = location.href.match(/sn=(\\d+)/);
  if (!m) return;
  var sn = m[1];
  var titleEl = document.querySelector('.anime_name h1') || document.querySelector('.anime_name');
  var title = (titleEl ? titleEl.textContent : document.title).trim();
  var btn = document.createElement('button');
  btn.textContent = '📌 加入追番';
  btn.style.cssText = [
    'position:fixed', 'right:20px', 'bottom:20px', 'z-index:2147483647',
    'background:#409eff', 'color:#fff', 'border:0', 'padding:12px 20px',
    'border-radius:8px', 'font-size:14px', 'cursor:pointer',
    'box-shadow:0 4px 12px rgba(0,0,0,.15)',
    'font-family:system-ui, -apple-system, sans-serif'
  ].join(';');
  btn.onclick = function () {
    var u = 'ORIGIN_PLACEHOLDER/#/quick-add?sn=' + sn + '&title=' + encodeURIComponent(title);
    window.open(u, 'anibutler_quickadd', 'width=480,height=640');
  };
  document.body.appendChild(btn);
})();`

const BOOKMARKLET_TEMPLATE = `javascript:(function(){var m=location.href.match(/sn=(\\d+)/);if(!m){alert('此頁面沒有 sn 參數，不是動畫瘋作品頁');return}var t=(document.querySelector('.anime_name h1')||document).textContent.trim();window.open('ORIGIN_PLACEHOLDER/#/quick-add?sn='+m[1]+'&title='+encodeURIComponent(t),'anibutler_quickadd','width=480,height=640');})();`

const userscript = computed(() => USERSCRIPT_TEMPLATE.replace('ORIGIN_PLACEHOLDER', origin.value))
const bookmarklet = computed(() => BOOKMARKLET_TEMPLATE.replace('ORIGIN_PLACEHOLDER', origin.value))
// Same content as `bookmarklet` — kept as a separate computed so the
// template's intent (draggable link target) is self-documenting.
const bookmarkletHref = computed(() => bookmarklet.value)

async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已複製')
  } catch (err) {
    ElMessage.error(`複製失敗：${(err as Error).message}`)
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="瀏覽器擴充 / 快速加入工具"
    :width="isMobile ? '100%' : '640px'"
    :fullscreen="isMobile"
    class="ag-ext-dialog"
  >
    <el-alert
      v-if="isInsecureOrigin"
      type="warning"
      :closable="false"
      show-icon
      class="ag-ext-https-warning"
    >
      <template #title>
        目前是 HTTP 環境
      </template>
      bookmarklet / userscript 會 bake 當前的 http:// URL，之後你的通知 bot session cookie
      將透過 HTTP 傳輸，容易被中間人竊聽。強烈建議部署 HTTPS 後再產生腳本。
    </el-alert>

    <p class="ag-ext-intro">
      在動畫瘋作品頁一鍵加入追番清單。
    </p>

    <h3 class="ag-ext-heading">
      Tampermonkey 使用者腳本（推薦）
    </h3>
    <pre class="ag-code-block">{{ userscript }}</pre>
    <el-button
      size="small"
      @click="copyText(userscript)"
    >
      <el-icon><DocumentCopy /></el-icon>
      複製腳本
    </el-button>

    <ol class="ag-ext-steps">
      <li>
        Chrome / Edge / Firefox 安裝
        <a
          href="https://tampermonkey.net/"
          target="_blank"
          rel="noopener noreferrer"
        >Tampermonkey</a>
      </li>
      <li>點瀏覽器工具列的 Tampermonkey icon → 新增指令碼</li>
      <li>貼上並存檔 (Ctrl+S)</li>
      <li>重整動畫瘋作品頁 → 右下會出現「📌 加入追番」浮動按鈕</li>
    </ol>

    <hr class="ag-ext-divider" />

    <h3 class="ag-ext-heading">
      書籤列版本（免安裝，備用）
    </h3>
    <p>拖曳下方連結到書籤列即可：</p>
    <a
      class="ag-bookmarklet-link"
      :href="bookmarkletHref"
      @click.prevent
    >🔖 加入追番</a>
    <p>或按下方複製 JavaScript：</p>
    <pre class="ag-code-block">{{ bookmarklet }}</pre>
    <el-button
      size="small"
      @click="copyText(bookmarklet)"
    >
      <el-icon><DocumentCopy /></el-icon>
      複製書籤
    </el-button>

    <template #footer>
      <el-button @click="visible = false">
        關閉
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.ag-ext-https-warning {
  margin: 0 0 16px;
}
.ag-ext-intro {
  margin: 0 0 12px;
  font-size: 14px;
}
.ag-ext-heading {
  font-size: 14px;
  margin: 16px 0 8px;
}
.ag-code-block {
  background: #f5f7fa;
  padding: 12px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 240px;
  overflow-y: auto;
}
.ag-ext-steps {
  font-size: 13px;
  line-height: 1.8;
  padding-left: 20px;
  margin: 8px 0;
}
.ag-ext-divider {
  border: none;
  border-top: 1px solid var(--el-border-color, #e4e7ed);
  margin: 20px 0;
}
.ag-bookmarklet-link {
  display: inline-block;
  margin: 8px 0;
  padding: 8px 16px;
  background: #409eff;
  color: #fff;
  border-radius: 6px;
  text-decoration: none;
  font-size: 13px;
  cursor: grab;
}
</style>
