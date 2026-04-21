<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { AnimeListApi } from '@/api/animelist'
import DirtyFab from '@/components/DirtyFab.vue'
import { useAuthStore } from '@/stores/auth'
import type { AnimeListEntry, AnimeListMode } from '@/types'

const api = new AnimeListApi()
const { isAdmin, user } = useAuthStore()

// --- Tag draft map ---
// reactive(Map) — Vue 3 proxies get/has/set/delete so mutations trigger
// re-renders.  WeakMap is not reactive (keys aren't enumerable).
// Map is keyed by object identity so sn=0 collisions never occur.
const tagDrafts = reactive(new Map<AnimeListEntry, string>())

function getTagValue(row: AnimeListEntry): string {
  return tagDrafts.has(row) ? (tagDrafts.get(row) as string) : row.tag
}

function setTagDraft(row: AnimeListEntry, value: string): void {
  tagDrafts.set(row, value)
}

function commitTagDraft(row: AnimeListEntry): void {
  if (tagDrafts.has(row)) {
    const draft = tagDrafts.get(row) as string
    if (draft !== row.tag) {
      row.tag = draft
    }
    tagDrafts.delete(row)
  }
}

// --- Custom name draft map ---
const customNameDrafts = reactive(new Map<AnimeListEntry, string>())

function getCustomNameValue(row: AnimeListEntry): string {
  return customNameDrafts.has(row) ? (customNameDrafts.get(row) as string) : (row.custom_name ?? '')
}

function setCustomNameDraft(row: AnimeListEntry, value: string): void {
  customNameDrafts.set(row, value)
}

function commitCustomNameDraft(row: AnimeListEntry): void {
  if (customNameDrafts.has(row)) {
    const draft = customNameDrafts.get(row) as string
    const normalised = draft.trim() === '' ? null : draft
    if (normalised !== row.custom_name) {
      row.custom_name = normalised
    }
    customNameDrafts.delete(row)
  }
}

const entries = ref<AnimeListEntry[]>([])
const original = ref<string>('[]')
const loading = ref(false)
const saving = ref(false)
const activeGroups = ref<string[]>([])

const MODES: { value: AnimeListMode; label: string }[] = [
  { value: 'single', label: '僅本集 (single)' },
  { value: 'latest', label: '最後一集 (latest)' },
  { value: 'all', label: '全部劇集 (all)' },
  { value: 'largest-sn', label: '最近上傳 (largest-sn)' },
]

const UNGROUPED_KEY = ''
const UNGROUPED_LABEL = '未分類'

const dirty = computed(() => JSON.stringify(entries.value) !== original.value)

// ---------------------------------------------------------------------------
// Admin: group by owning user (sections), then by tag within each section.
// Non-admin: group by tag only (existing behaviour).
// ---------------------------------------------------------------------------

interface UserSection {
  /** Stable key for v-for / el-collapse. Equals owner_id, or '' for admin's own. */
  userId: string
  /** Human-readable label for the section header. */
  username: string
  /** True if this section belongs to the currently logged-in admin. */
  isSelf: boolean
  /** Tag groups within this user section. */
  tagGroups: { tag: string; rows: AnimeListEntry[] }[]
  totalCount: number
}

/**
 * For admin view: returns sections sorted — self first, others alphabetically.
 */
const adminSections = computed((): UserSection[] => {
  if (!isAdmin.value) return []

  // Collect per-user entry lists, preserving entry order.
  const userMap = new Map<string, { username: string; entries: AnimeListEntry[] }>()

  for (const entry of entries.value) {
    const uid = entry.owner_id ?? ''
    const uname = (entry.owner_username ?? uid) || '(unknown)'
    if (!userMap.has(uid)) {
      userMap.set(uid, { username: uname, entries: [] })
    }
    userMap.get(uid)!.entries.push(entry)
  }

  const selfId = user.value?.id ?? ''

  const sections: UserSection[] = []
  for (const [uid, { username, entries: userEntries }] of userMap) {
    const isSelf = uid === selfId
    // Build tag groups within this user's entries.
    const tagMap = new Map<string, AnimeListEntry[]>()
    for (const entry of userEntries) {
      const key = entry.tag ?? UNGROUPED_KEY
      if (!tagMap.has(key)) tagMap.set(key, [])
      tagMap.get(key)!.push(entry)
    }
    const tagGroups = Array.from(tagMap.entries()).map(([tag, rows]) => ({ tag, rows }))
    sections.push({ userId: uid, username, isSelf, tagGroups, totalCount: userEntries.length })
  }

  // Sort: self first, then alphabetical by username.
  sections.sort((a, b) => {
    if (a.isSelf && !b.isSelf) return -1
    if (!a.isSelf && b.isSelf) return 1
    return a.username.localeCompare(b.username)
  })

  return sections
})

/**
 * For non-admin view: flat tag grouping (existing behaviour).
 */
const groupedEntries = computed(() => {
  const groups = new Map<string, AnimeListEntry[]>()
  for (const entry of entries.value) {
    const key = entry.tag ?? UNGROUPED_KEY
    if (!groups.has(key)) {
      groups.set(key, [])
    }
    groups.get(key)!.push(entry)
  }
  // Preserve first-seen order of tags.
  return Array.from(groups.entries()).map(([tag, rows]) => ({ tag, rows }))
})

// Collapse keys for admin mode: one per user section.
const adminActiveSections = ref<string[]>([])

// Auto-expand groups that appear after the initial load (e.g. user
// types a new tag into the 群組 column). We only _add_ to
// activeGroups — collapsing a group the user manually closed would be
// hostile.
watch(
  groupedEntries,
  (next, prev) => {
    if (!prev) return
    const prevKeys = new Set(prev.map((g) => g.tag))
    const newlyAppeared: string[] = []
    for (const group of next) {
      if (!prevKeys.has(group.tag) && !activeGroups.value.includes(group.tag)) {
        newlyAppeared.push(group.tag)
      }
    }
    if (newlyAppeared.length > 0) {
      activeGroups.value = [...activeGroups.value, ...newlyAppeared]
    }
  },
)

function snapshot(list: AnimeListEntry[]): string {
  return JSON.stringify(list)
}

function clone(list: AnimeListEntry[]): AnimeListEntry[] {
  return list.map((e) => ({ ...e }))
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const payload = await api.list()
    entries.value = clone(payload.entries ?? [])
    original.value = snapshot(entries.value)
    activeGroups.value = groupedEntries.value.map((g) => g.tag)
    adminActiveSections.value = adminSections.value.map((s) => s.userId)
  } catch (err) {
    ElMessage.error(`讀取追番清單失敗：${(err as Error).message}`)
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  try {
    await api.replaceAll(entries.value)
    ElMessage.success('追番清單已儲存')
    await load()
  } catch (err) {
    ElMessage.error(`追番清單儲存失敗：${(err as Error).message}`)
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
  entries.value = clone(JSON.parse(original.value) as AnimeListEntry[])
}

function addEntry(): void {
  const blank: AnimeListEntry = {
    sn: 0,
    enabled: true,
    mode: null,
    tag: '',
    season: 1,
    custom_name: null,
    comment: '',
    anime_name: null,
    downloaded_episodes: 0,
    known_episodes: 0,
    owner_id: user.value?.id ?? null,
    owner_username: null,
  }
  entries.value = [blank, ...entries.value]
  if (!activeGroups.value.includes('')) {
    activeGroups.value = [...activeGroups.value, '']
  }
}

function removeEntry(entry: AnimeListEntry): void {
  const idx = entries.value.indexOf(entry)
  if (idx !== -1) {
    entries.value.splice(idx, 1)
  }
}

function groupLabel(tag: string): string {
  return tag === UNGROUPED_KEY ? UNGROUPED_LABEL : tag
}

function episodeText(entry: AnimeListEntry): string {
  if (
    entry.anime_name === null &&
    entry.downloaded_episodes === 0 &&
    entry.known_episodes === 0
  ) {
    return '—'
  }
  return `${entry.downloaded_episodes} / ${entry.known_episodes}`
}

/** Unique collapse key for a (userSection, tag) combo in admin mode. */
function adminTagKey(userId: string, tag: string): string {
  return `${userId}::${tag}`
}

onMounted(load)
</script>

<template>
  <div class="ag-container">
    <h1 class="ag-section-title">
      追番清單
    </h1>

    <el-alert
      type="info"
      :closable="false"
      class="ag-section"
    >
      <template #title>
        <div class="ag-help">
          <div>
            每列代表一部追蹤中的番劇。可直接在表格中編輯
            <strong>群組</strong>（@分類）、<strong>啟用 / 停用</strong>、<strong>下載模式</strong>、<strong>季</strong>、
            <strong>自訂名稱</strong>與 <strong>註釋</strong>。
          </div>
          <ul>
            <li>群組欄位即原始檔案中的 <code>@分類</code>，留空代表未分類。</li>
            <li>下載模式留空表示使用設定頁的預設模式。</li>
            <li>「季」欄位決定檔名格式，例如 S01E01。預設為 1。</li>
            <li>「自訂名稱」可覆蓋下載後檔名中使用的番劇名稱；留空則自動使用偵測到的名稱。</li>
            <li>番劇名稱與集數由後端根據掃描結果自動填入，無法手動編輯。</li>
          </ul>
        </div>
      </template>
    </el-alert>

    <div class="ag-toolbar">
      <el-button
        type="primary"
        @click="addEntry"
      >
        新增項目
      </el-button>
    </div>

    <!-- ====== ADMIN VIEW: grouped by user ====== -->
    <template v-if="isAdmin">
      <div
        v-for="section in adminSections"
        :key="section.userId"
        class="ag-user-section"
      >
        <!-- User section header -->
        <div class="ag-user-header">
          <span class="ag-user-icon">👤</span>
          <span class="ag-user-name">{{ section.username }}</span>
          <span
            v-if="section.isSelf"
            class="ag-user-self-badge"
          >（我）</span>
          <span class="ag-user-count">{{ section.totalCount }} 部作品</span>
        </div>

        <!-- Tag sub-groups within this user -->
        <el-collapse
          v-model="adminActiveSections"
          class="ag-section"
        >
          <el-collapse-item
            v-for="group in section.tagGroups"
            :key="adminTagKey(section.userId, group.tag)"
            :name="adminTagKey(section.userId, group.tag)"
            :title="`${groupLabel(group.tag)}（${group.rows.length}）`"
          >
            <el-table
              :data="group.rows"
              stripe
              size="small"
              class="ag-anime-table"
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
                label="sn"
                width="100"
              >
                <template #default="{ row }">
                  <el-input-number
                    v-model="row.sn"
                    :min="0"
                    :controls="false"
                    size="small"
                    class="ag-sn-input"
                  />
                </template>
              </el-table-column>
              <el-table-column
                label="番劇名稱"
                min-width="180"
              >
                <template #default="{ row }">
                  <span v-if="row.anime_name">{{ row.anime_name }}</span>
                  <span
                    v-else
                    class="ag-muted"
                  >（尚未下載）</span>
                </template>
              </el-table-column>
              <el-table-column
                label="自訂名稱"
                width="160"
              >
                <template #default="{ row }">
                  <el-input
                    :model-value="getCustomNameValue(row)"
                    placeholder="（預設使用抓到的名稱）"
                    size="small"
                    @update:model-value="setCustomNameDraft(row, $event)"
                    @blur="commitCustomNameDraft(row)"
                    @keyup.enter="commitCustomNameDraft(row)"
                  />
                </template>
              </el-table-column>
              <el-table-column
                label="下載模式"
                width="160"
              >
                <template #default="{ row }">
                  <el-select
                    v-model="row.mode"
                    placeholder="使用預設"
                    clearable
                    size="small"
                  >
                    <el-option
                      v-for="m in MODES"
                      :key="m.value"
                      :label="m.label"
                      :value="m.value"
                    />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column
                label="季"
                width="80"
              >
                <template #default="{ row }">
                  <el-input-number
                    v-model="row.season"
                    :min="1"
                    :step="1"
                    :controls="false"
                    size="small"
                    class="ag-season-input"
                  />
                </template>
              </el-table-column>
              <el-table-column
                label="註釋"
                min-width="140"
              >
                <template #default="{ row }">
                  <el-input
                    v-model="row.comment"
                    placeholder="（可空）"
                    size="small"
                  />
                </template>
              </el-table-column>
              <el-table-column
                label="集數"
                width="90"
              >
                <template #default="{ row }">
                  <span class="ag-episode">{{ episodeText(row) }}</span>
                </template>
              </el-table-column>
              <el-table-column
                label="群組"
                width="140"
              >
                <template #default="{ row }">
                  <el-input
                    :model-value="getTagValue(row)"
                    placeholder="（未分類）"
                    size="small"
                    @update:model-value="setTagDraft(row, $event)"
                    @blur="commitTagDraft(row)"
                    @keyup.enter="commitTagDraft(row)"
                  />
                </template>
              </el-table-column>
              <el-table-column
                label="操作"
                width="80"
              >
                <template #default="{ row }">
                  <el-button
                    size="small"
                    type="danger"
                    link
                    @click="removeEntry(row)"
                  >
                    刪除
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-collapse-item>
        </el-collapse>
      </div>
    </template>

    <!-- ====== NON-ADMIN VIEW: tag grouping only (unchanged) ====== -->
    <el-collapse
      v-else
      v-model="activeGroups"
      class="ag-section"
    >
      <el-collapse-item
        v-for="group in groupedEntries"
        :key="group.tag"
        :name="group.tag"
        :title="`${groupLabel(group.tag)}（${group.rows.length}）`"
      >
        <el-table
          :data="group.rows"
          stripe
          size="small"
          class="ag-anime-table"
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
            label="sn"
            width="100"
          >
            <template #default="{ row }">
              <el-input-number
                v-model="row.sn"
                :min="0"
                :controls="false"
                size="small"
                class="ag-sn-input"
              />
            </template>
          </el-table-column>
          <el-table-column
            label="番劇名稱"
            min-width="180"
          >
            <template #default="{ row }">
              <span v-if="row.anime_name">{{ row.anime_name }}</span>
              <span
                v-else
                class="ag-muted"
              >（尚未下載）</span>
            </template>
          </el-table-column>
          <el-table-column
            label="自訂名稱"
            width="160"
          >
            <template #default="{ row }">
              <el-input
                :model-value="getCustomNameValue(row)"
                placeholder="（預設使用抓到的名稱）"
                size="small"
                @update:model-value="setCustomNameDraft(row, $event)"
                @blur="commitCustomNameDraft(row)"
                @keyup.enter="commitCustomNameDraft(row)"
              />
            </template>
          </el-table-column>
          <el-table-column
            label="下載模式"
            width="160"
          >
            <template #default="{ row }">
              <el-select
                v-model="row.mode"
                placeholder="使用預設"
                clearable
                size="small"
              >
                <el-option
                  v-for="m in MODES"
                  :key="m.value"
                  :label="m.label"
                  :value="m.value"
                />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column
            label="季"
            width="80"
          >
            <template #default="{ row }">
              <el-input-number
                v-model="row.season"
                :min="1"
                :step="1"
                :controls="false"
                size="small"
                class="ag-season-input"
              />
            </template>
          </el-table-column>
          <el-table-column
            label="註釋"
            min-width="140"
          >
            <template #default="{ row }">
              <el-input
                v-model="row.comment"
                placeholder="（可空）"
                size="small"
              />
            </template>
          </el-table-column>
          <el-table-column
            label="集數"
            width="90"
          >
            <template #default="{ row }">
              <span class="ag-episode">{{ episodeText(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column
            label="群組"
            width="140"
          >
            <template #default="{ row }">
              <el-input
                :model-value="getTagValue(row)"
                placeholder="（未分類）"
                size="small"
                @update:model-value="setTagDraft(row, $event)"
                @blur="commitTagDraft(row)"
                @keyup.enter="commitTagDraft(row)"
              />
            </template>
          </el-table-column>
          <el-table-column
            label="操作"
            width="80"
          >
            <template #default="{ row }">
              <el-button
                size="small"
                type="danger"
                link
                @click="removeEntry(row)"
              >
                刪除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-collapse-item>
    </el-collapse>

    <div
      v-if="!loading && entries.length === 0"
      class="ag-empty"
    >
      目前追番清單為空，點擊上方「新增項目」開始追蹤。
    </div>

    <DirtyFab
      :visible="dirty"
      :saving="saving"
      @save="save"
      @discard="discard"
    />
  </div>
</template>

<style scoped>
.ag-help {
  font-size: 13px;
  line-height: 1.7;
  color: var(--ag-text);
}
.ag-help ul {
  margin: 6px 0 0;
  padding-left: 20px;
}
.ag-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.ag-anime-table {
  width: 100%;
}
.ag-sn-input {
  width: 100%;
}
.ag-season-input {
  width: 100%;
}
.ag-muted {
  color: #9ca3af;
}
.ag-episode {
  font-variant-numeric: tabular-nums;
}
.ag-empty {
  text-align: center;
  color: #9ca3af;
  padding: 32px 0;
}

/* Admin user-section styles */
.ag-user-section {
  margin-bottom: 24px;
}
.ag-user-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0 6px;
  border-bottom: 2px solid var(--el-border-color, #e4e7ed);
  margin-bottom: 8px;
}
.ag-user-icon {
  font-size: 16px;
}
.ag-user-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary, #303133);
}
.ag-user-self-badge {
  font-size: 12px;
  color: var(--el-color-primary, #409eff);
  font-weight: 500;
}
.ag-user-count {
  margin-left: auto;
  font-size: 12px;
  color: #9ca3af;
}
</style>
