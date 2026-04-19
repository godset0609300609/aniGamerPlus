<script setup lang="ts">
/**
 * Compatibility re-export — LogStreamPanel has moved to @/components/LogStreamPanel.vue.
 * This shim keeps existing imports from @/components/monitor/LogStreamPanel working.
 */
import LogStreamPanel from '@/components/LogStreamPanel.vue'

const { alwaysExpanded = false } = defineProps<{
  alwaysExpanded?: boolean
}>()
</script>

<template>
  <LogStreamPanel :always-expanded="alwaysExpanded" />
</template>
