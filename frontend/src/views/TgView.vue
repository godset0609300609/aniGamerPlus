<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ChatsTab from './tg/ChatsTab.vue'
import DownloadsTab from './tg/DownloadsTab.vue'

// Order matters — it defines "later" vs "earlier" for the slide direction.
// Mirrors BtView.vue's tab-slide pattern exactly, so the two feel consistent.
const TABS = ['chats', 'downloads']

const route = useRoute()
const router = useRouter()
const activeTab = ref(typeof route.query.tab === 'string' ? route.query.tab : 'chats')

const slideDir = ref<'left' | 'right'>('left')

watch(activeTab, (next, prev) => {
  const nextIndex = TABS.indexOf(next)
  const prevIndex = TABS.indexOf(prev)
  if (nextIndex !== -1 && prevIndex !== -1) {
    slideDir.value = nextIndex > prevIndex ? 'left' : 'right'
  }
})

watch(
  () => route.query.tab,
  (tab) => {
    if (typeof tab === 'string' && tab) activeTab.value = tab
  },
)

watch(activeTab, (tab) => {
  if (route.query.tab === tab) return
  router.replace({ path: '/tg', query: { tab } })
})
</script>

<template>
  <div class="ag-container">
    <h1 class="ag-section-title">
      Telegram 下載
    </h1>

    <el-tabs v-model="activeTab">
      <el-tab-pane
        label="監控 Chat"
        name="chats"
      />
      <el-tab-pane
        label="下載紀錄"
        name="downloads"
      />
    </el-tabs>

    <transition
      :name="`slide-${slideDir}`"
      mode="out-in"
    >
      <keep-alive>
        <ChatsTab
          v-if="activeTab === 'chats'"
          key="chats"
        />
        <DownloadsTab
          v-else-if="activeTab === 'downloads'"
          key="downloads"
        />
      </keep-alive>
    </transition>
  </div>
</template>

<style scoped>
.slide-left-enter-active,
.slide-left-leave-active,
.slide-right-enter-active,
.slide-right-leave-active {
  transition: opacity 220ms cubic-bezier(0.4, 0, 0.2, 1),
    transform 220ms cubic-bezier(0.4, 0, 0.2, 1);
}
.slide-left-enter-from {
  opacity: 0;
  transform: translateX(24px);
}
.slide-left-leave-to {
  opacity: 0;
  transform: translateX(-24px);
}
.slide-right-enter-from {
  opacity: 0;
  transform: translateX(-24px);
}
.slide-right-leave-to {
  opacity: 0;
  transform: translateX(24px);
}
</style>
