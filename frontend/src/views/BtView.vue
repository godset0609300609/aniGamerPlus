<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import FiltersTab from './bt/FiltersTab.vue'
import FeedsTab from './bt/FeedsTab.vue'
import EntriesTab from './bt/EntriesTab.vue'

// Order matters — it defines "later" vs "earlier" for the slide direction.
const TABS = ['filters', 'feeds', 'entries']

const route = useRoute()
const router = useRouter()
const activeTab = ref(typeof route.query.tab === 'string' ? route.query.tab : 'filters')

// Direction of the panel transition: 'left' when moving to a later tab,
// 'right' when moving to an earlier one. Driven by a watcher (rather than
// an @tab-change handler) so it reacts uniformly whether activeTab changed
// via a nav click or via the ?tab= query-param sync below.
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
  router.replace({ path: '/bt', query: { tab } })
})
</script>

<template>
  <div class="ag-container">
    <h1 class="ag-section-title">
      BT 下載
    </h1>

    <!--
      El-tabs is used for the nav header only — its panes are left empty.
      Element Plus keeps every visited pane mounted (toggled via v-show)
      once activated, so a <transition> placed inside a pane never sees a
      mount/unmount and would not animate on tab switches. The actual tab
      content is rendered separately below, gated by v-if, so exactly one
      child component is active at a time and the slide transition fires
      on every switch.

      <keep-alive> sits inside the <transition> (Vue's documented pattern
      for this combo) so switching tabs still plays the enter/leave slide,
      but each tab's component is deactivated rather than destroyed —
      preserving its loaded data / in-progress edits (e.g. an unsaved
      filter draft, an entries search query) instead of refetching and
      resetting on every visit.
    -->
    <el-tabs v-model="activeTab">
      <el-tab-pane
        label="過濾器"
        name="filters"
      />
      <el-tab-pane
        label="RSS 來源"
        name="feeds"
      />
      <el-tab-pane
        label="抓取紀錄"
        name="entries"
      />
    </el-tabs>

    <transition
      :name="`slide-${slideDir}`"
      mode="out-in"
    >
      <keep-alive>
        <FiltersTab
          v-if="activeTab === 'filters'"
          key="filters"
        />
        <FeedsTab
          v-else-if="activeTab === 'feeds'"
          key="feeds"
        />
        <EntriesTab
          v-else-if="activeTab === 'entries'"
          key="entries"
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
