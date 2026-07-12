<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { TgApi } from '@/api/tg'
import { formatRelativeBare } from '@/utils/format'
import { useAutoRefresh } from '@/composables/useAutoRefresh'
import { useBreakpoint } from '@/composables/useBreakpoint'
import type { TgDownloadedMedia } from '@/types'

const { isMobile } = useBreakpoint()

const PAGE_SIZES = [10, 20, 50, 100]

const api = new TgApi()

const items = ref<TgDownloadedMedia[]>([])
const loading = ref(false)
const page = ref(1)
const size = ref(50)
const total = ref(0)

function formatSize(bytes: number): string {
  if (bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await api.listDownloads(page.value, size.value)
    items.value = result.items
    total.value = result.total
  } catch (err) {
    ElMessage.error(`讀取下載紀錄失敗：${(err as Error).message}`)
  } finally {
    loading.value = false
  }
}

function handleSizeChange(newSize: number): void {
  size.value = newSize
  page.value = 1
  void load()
}

function handleCurrentChange(newPage: number): void {
  page.value = newPage
  void load()
}

onMounted(load)

// Fix 5 — live-refresh so newly-downloaded media shows up while a user is
// parked on this tab watching TG downloads land.
useAutoRefresh(5000, load)
</script>

<template>
  <div class="ag-tg-downloads">
    <div class="ag-toolbar">
      <el-button
        :loading="loading"
        @click="load"
      >
        重新整理
      </el-button>
    </div>

    <el-table
      v-if="!isMobile"
      :data="items"
      stripe
      size="small"
      class="ag-downloads-table"
      empty-text=" "
    >
      <el-table-column
        label="檔名"
        min-width="220"
        :show-overflow-tooltip="true"
      >
        <template #default="{ row }">
          {{ row.file_name }}
        </template>
      </el-table-column>
      <el-table-column
        label="Chat"
        min-width="160"
      >
        <template #default="{ row }">
          {{ row.chat_title ?? row.chat_id }}
        </template>
      </el-table-column>
      <el-table-column
        label="大小"
        width="100"
        align="right"
      >
        <template #default="{ row }">
          {{ formatSize(row.file_size) }}
        </template>
      </el-table-column>
      <el-table-column
        label="時間"
        width="140"
        align="right"
      >
        <template #default="{ row }">
          <el-tooltip
            :content="row.downloaded_at"
            placement="top"
          >
            <span>{{ formatRelativeBare(row.downloaded_at) }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column
        label="檔案名稱"
        min-width="240"
        :show-overflow-tooltip="true"
      >
        <template #default="{ row }">
          {{ row.local_path }}
        </template>
      </el-table-column>
    </el-table>

    <!-- Mobile: stacked cards instead of a cramped table. -->
    <div
      v-else
      class="ag-download-cards"
    >
      <div
        v-for="row in items"
        :key="row.id"
        class="ag-download-card"
      >
        <div class="ag-download-card__title">
          {{ row.file_name }}
        </div>
        <div class="ag-download-card__meta">
          <span>{{ row.chat_title ?? row.chat_id }}</span>
          <span>{{ formatSize(row.file_size) }}</span>
        </div>
        <div class="ag-download-card__meta">
          <el-tooltip
            :content="row.downloaded_at"
            placement="top"
          >
            <span>{{ formatRelativeBare(row.downloaded_at) }}</span>
          </el-tooltip>
        </div>
        <div class="ag-download-card__path">
          {{ row.local_path }}
        </div>
      </div>
    </div>

    <div
      v-if="!loading && items.length === 0"
      class="ag-empty"
    >
      還沒有任何下載紀錄。
    </div>

    <el-pagination
      :total="total"
      :current-page="page"
      :page-size="size"
      :page-sizes="PAGE_SIZES"
      layout="total, sizes, prev, pager, next"
      class="ag-pagination"
      @size-change="handleSizeChange"
      @current-change="handleCurrentChange"
    />
  </div>
</template>

<style scoped>
.ag-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.ag-downloads-table {
  width: 100%;
}
.ag-empty {
  text-align: center;
  color: #9ca3af;
  padding: 32px 0;
}
.ag-pagination {
  margin-top: 16px;
  justify-content: flex-end;
}

.ag-download-cards {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.ag-download-card {
  border: 1px solid var(--el-border-color, #e4e7ed);
  border-radius: 8px;
  padding: 12px;
  background: var(--el-bg-color, #fff);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ag-download-card__title {
  font-weight: 600;
  word-break: break-word;
}
.ag-download-card__meta {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.ag-download-card__path {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  word-break: break-all;
}
</style>
