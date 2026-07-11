<script setup lang="ts">
import { computed, onActivated, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { BtApi } from '@/api/bt'
import DirtyFab from '@/components/DirtyFab.vue'
import { useAutoRefresh } from '@/composables/useAutoRefresh'
import { useBreakpoint } from '@/composables/useBreakpoint'
import FiltersImportDialog from './FiltersImportDialog.vue'
import type { BtFilter } from '@/types'

const api = new BtApi()
const router = useRouter()
const { isMobile } = useBreakpoint()

function viewMatches(row: BtFilter): void {
  void router.push({ path: '/bt', query: { tab: 'entries', filter: String(row.id) } })
}

const filters = ref<BtFilter[]>([])
const original = ref<string>('[]')
const loading = ref(false)
const saving = ref(false)

const dirty = computed(() => JSON.stringify(filters.value) !== original.value)

function snapshot(list: BtFilter[]): string {
  return JSON.stringify(list)
}

function clone(list: BtFilter[]): BtFilter[] {
  return list.map((f) => ({ ...f, keywords: [...f.keywords] }))
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await api.listFilters()
    filters.value = clone(data ?? [])
    original.value = snapshot(filters.value)
  } catch (err) {
    ElMessage.error(`讀取過濾器失敗：${(err as Error).message}`)
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  try {
    await api.replaceFilters(filters.value)
    ElMessage.success('過濾器已儲存')
    await load()
  } catch (err) {
    ElMessage.error(`過濾器儲存失敗：${(err as Error).message}`)
  } finally {
    saving.value = false
  }
}

async function discard(): Promise<void> {
  if (!dirty.value) return
  try {
    await ElMessageBox.confirm('捨棄目前未儲存的變更？', '放棄變更', {
      confirmButtonText: '確定',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  filters.value = clone(JSON.parse(original.value) as BtFilter[])
}

const nextSortOrder = computed(() => filters.value.reduce((m, f) => Math.max(m, f.sort_order), -1) + 1)

function addFilter(): void {
  const blank: BtFilter = {
    id: -Date.now(),
    name: '',
    keywords: [],
    enabled: true,
    sort_order: nextSortOrder.value,
    created_at: '',
    updated_at: '',
  }
  filters.value = [...filters.value, blank]
}

function removeFilter(row: BtFilter): void {
  const idx = filters.value.indexOf(row)
  if (idx !== -1) {
    filters.value.splice(idx, 1)
  }
}

// --- Import wizard ---
const importDialogVisible = ref(false)

function handleFilterCreated(filter: BtFilter): void {
  filters.value = [...filters.value, filter]
}

// --- Keyword tag editing (per-row "add keyword" input) ---
const keywordInputVisible = reactive(new Map<BtFilter, boolean>())
const keywordDrafts = reactive(new Map<BtFilter, string>())

function isKeywordInputVisible(row: BtFilter): boolean {
  return keywordInputVisible.get(row) === true
}

function showKeywordInput(row: BtFilter): void {
  keywordInputVisible.set(row, true)
  keywordDrafts.set(row, '')
}

function getKeywordDraft(row: BtFilter): string {
  return keywordDrafts.get(row) ?? ''
}

function setKeywordDraft(row: BtFilter, value: string): void {
  keywordDrafts.set(row, value)
}

function commitKeyword(row: BtFilter): void {
  const draft = (keywordDrafts.get(row) ?? '').trim()
  if (draft) {
    row.keywords = [...row.keywords, draft]
  }
  keywordInputVisible.set(row, false)
  keywordDrafts.delete(row)
}

function removeKeyword(row: BtFilter, index: number): void {
  row.keywords = row.keywords.filter((_, i) => i !== index)
}

onMounted(load)

// BtView keeps every visited tab alive via <keep-alive> rather than
// remounting it on every switch, so a filter imported from EntriesTab's
// "匯入過濾器" dialog would otherwise stay invisible here until a manual
// refresh. Refetch whenever this tab becomes active again — but only when
// there are no unsaved local edits, so an in-progress rename/keyword-add
// isn't silently discarded by an automatic background refetch.
//
// Vue fires `activated` once for the *initial* insertion into a <keep-alive>
// boundary too (not just on cache-restore), which would otherwise race the
// onMounted() load() above and double-fetch on first visit. Skip that first
// call — only real reactivations (tab switches) should trigger a refetch.
let skipNextActivation = true

onActivated(() => {
  if (skipNextActivation) {
    skipNextActivation = false
    return
  }
  if (dirty.value) return
  void load()
})

// Fix 5 — live-refresh polling, same dirty-guard as the onActivated refetch
// above: never clobber an in-progress rename/keyword-add with a background
// refetch.
function autoRefetch(): void {
  if (dirty.value) return
  void load()
}

useAutoRefresh(5000, autoRefetch)
</script>

<template>
  <div class="ag-bt-filters">
    <el-alert
      type="info"
      :closable="false"
      class="ag-section"
    >
      <template #title>
        <div class="ag-help">
          每列代表一組過濾條件，命中的 RSS 項目才會送往 Put.io 下載。
          <strong>關鍵字</strong>之間為「AND」關係（全部符合才算命中）。
        </div>
      </template>
    </el-alert>

    <div class="ag-toolbar">
      <el-button
        type="primary"
        @click="addFilter"
      >
        新增過濾器
      </el-button>
      <el-button @click="importDialogVisible = true">
        從標題匯入
      </el-button>
    </div>

    <!-- Horizontal-scroll wrapper: on mobile a low-priority column (順序)
         is hidden below, but the remaining columns still need more room
         than a 375px viewport gives, so the table scrolls within this
         container rather than overflowing the page. -->
    <div class="ag-table-scroll">
      <el-table
        :data="filters"
        stripe
        size="small"
        class="ag-filters-table"
        empty-text=" "
      >
        <el-table-column
          label="啟用"
          width="70"
        >
          <template #default="{ row }">
            <el-switch v-model="row.enabled" />
          </template>
        </el-table-column>

        <el-table-column
          label="名稱"
          min-width="160"
        >
          <template #default="{ row }">
            <el-input
              v-model="row.name"
              placeholder="過濾器名稱"
              size="small"
            />
          </template>
        </el-table-column>

        <el-table-column
          label="關鍵字（AND）"
          min-width="260"
        >
          <template #default="{ row }">
            <div class="ag-keywords">
              <el-tag
                v-for="(kw, idx) in row.keywords"
                :key="`${kw}-${idx}`"
                closable
                class="ag-keyword-tag"
                @close="removeKeyword(row, idx)"
              >
                {{ kw }}
              </el-tag>
              <el-input
                v-if="isKeywordInputVisible(row)"
                :model-value="getKeywordDraft(row)"
                size="small"
                class="ag-keyword-input"
                autofocus
                @update:model-value="setKeywordDraft(row, $event)"
                @keyup.enter="commitKeyword(row)"
                @blur="commitKeyword(row)"
              />
              <el-button
                v-else
                size="small"
                class="ag-keyword-add-btn"
                @click="showKeywordInput(row)"
              >
                ＋ 關鍵字
              </el-button>
            </div>
          </template>
        </el-table-column>

        <!-- Low priority on mobile — hidden below the mobile breakpoint so
             the higher-priority columns need less horizontal scroll. -->
        <el-table-column
          v-if="!isMobile"
          label="順序"
          width="120"
        >
          <template #default="{ row }">
            <el-input-number
              v-model="row.sort_order"
              style="width: 90px"
              :min="0"
              :controls="false"
              size="small"
            />
          </template>
        </el-table-column>

        <el-table-column
          label="操作"
          width="160"
        >
          <template #default="{ row }">
            <el-tooltip
              content="檢視此過濾器命中的抓取紀錄"
              placement="top"
            >
              <el-button
                size="small"
                link
                class="ag-view-matches-btn"
                @click="viewMatches(row)"
              >
                查看命中
              </el-button>
            </el-tooltip>
            <el-button
              size="small"
              type="danger"
              link
              @click="removeFilter(row)"
            >
              刪除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div
      v-if="!loading && filters.length === 0"
      class="ag-empty"
    >
      目前沒有任何過濾器，點擊上方「新增過濾器」開始設定。
    </div>

    <DirtyFab
      :visible="dirty"
      :saving="saving"
      @save="save"
      @discard="discard"
    />

    <FiltersImportDialog
      v-model="importDialogVisible"
      :next-sort-order="nextSortOrder"
      @filter-created="handleFilterCreated"
    />
  </div>
</template>

<style scoped>
.ag-help {
  font-size: 13px;
  line-height: 1.7;
}
.ag-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.ag-table-scroll {
  width: 100%;
  overflow-x: auto;
}
.ag-filters-table {
  width: 100%;
}
.ag-keywords {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.ag-keyword-tag {
  margin: 0;
}
.ag-keyword-input {
  width: 120px;
}
.ag-empty {
  text-align: center;
  color: #9ca3af;
  padding: 32px 0;
}
</style>
