<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Grid } from '@element-plus/icons-vue'
import { AnimeListApi } from '@/api/animelist'
import DirtyFab from '@/components/DirtyFab.vue'
import BrowserExtensionDialog from '@/components/animeList/BrowserExtensionDialog.vue'
import { useAuthStore } from '@/stores/auth'
import { useBreakpoint } from '@/composables/useBreakpoint'
import type { AnimeListEntry, AnimeListMode } from '@/types'

const extensionDialogOpen = ref(false)

const api = new AnimeListApi()
const { isAdmin, user } = useAuthStore()
const { isMobile } = useBreakpoint()

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
// Tracks whether the very first load() has completed. Only the initial
// fetch shows the skeleton placeholder — subsequent reloads (e.g. after
// save()) keep the existing rows visible instead of flashing back to a
// skeleton, matching the pattern used by MonitorView/SettingsView.
const hasLoadedOnce = ref(false)
const saving = ref(false)
const activeGroups = ref<string[]>([])

// ---------------------------------------------------------------------------
// localStorage persistence for collapse state (Fix 2)
// ---------------------------------------------------------------------------

const COLLAPSE_STORAGE_KEY = 'anigamerplus.animelist.collapse'

function loadPersistedCollapse(): string[] | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(COLLAPSE_STORAGE_KEY)
    if (raw === null) return null
    const parsed: unknown = JSON.parse(raw)
    if (
      typeof parsed === 'object' &&
      parsed !== null &&
      'open' in parsed &&
      Array.isArray((parsed as { open: unknown }).open)
    ) {
      return (parsed as { open: string[] }).open
    }
    return null
  } catch {
    return null
  }
}

function savePersistedCollapse(keys: string[]): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(COLLAPSE_STORAGE_KEY, JSON.stringify({ open: keys }))
  } catch {
    // silently ignore (e.g. storage quota exceeded)
  }
}

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
// Per-row write permission: admin can edit any row; non-admin only own rows.
// ---------------------------------------------------------------------------

function isOwnRow(row: AnimeListEntry): boolean {
  if (isAdmin.value) return true
  return row.owner_id === user.value?.id
}

// ---------------------------------------------------------------------------
// Both admin and non-admin now render the owner-grouped section view.
// ---------------------------------------------------------------------------

interface UserSection {
  /** Stable key for v-for / el-collapse. Equals owner_id, or '' for unknowns. */
  userId: string
  /** Human-readable label for the section header. */
  username: string
  /** True if this section belongs to the currently logged-in user. */
  isSelf: boolean
  /** Tag groups within this user section. */
  tagGroups: { tag: string; rows: AnimeListEntry[] }[]
  totalCount: number
}

/**
 * All users now see sections sorted — self first, others alphabetically.
 */
const allSections = computed((): UserSection[] => {
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

  // Sort: self first, then alphabetical by username (case-insensitive),
  // with '(unknown)' / empty userId pushed to the very end.
  sections.sort((a, b) => {
    if (a.isSelf && !b.isSelf) return -1
    if (!a.isSelf && b.isSelf) return 1
    const aUnknown = a.userId === ''
    const bUnknown = b.userId === ''
    if (aUnknown && !bUnknown) return 1
    if (!aUnknown && bUnknown) return -1
    return a.username.localeCompare(b.username, undefined, { sensitivity: 'base' })
  })

  return sections
})

/**
 * Keep groupedEntries for the watch() below (auto-expand new tags).
 * This is a flat tag-keyed view of all entries.
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
  return Array.from(groups.entries()).map(([tag, rows]) => ({ tag, rows }))
})

// Collapse keys for user sections: one per section (userId) + tag combo.
// Initialised with persisted value if available; otherwise filled on load().
const activeSections = ref<string[]>(loadPersistedCollapse() ?? [])

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

    // Build all collapse keys for the loaded sections.
    const allKeys: string[] = []
    for (const section of allSections.value) {
      for (const group of section.tagGroups) {
        allKeys.push(sectionTagKey(section.userId, group.tag))
      }
    }

    // First-time users (no persisted state): expand everything (existing behaviour).
    // Returning users: restore persisted state; stale/deleted keys are simply ignored.
    const persisted = loadPersistedCollapse()
    activeSections.value = persisted !== null ? persisted : allKeys
  } catch (err) {
    ElMessage.error(`讀取追番清單失敗：${(err as Error).message}`)
  } finally {
    loading.value = false
    hasLoadedOnce.value = true
  }
}

// Persist collapse state whenever it changes.
watch(activeSections, (keys) => {
  savePersistedCollapse(keys)
})

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

function addEntry(ownerSection?: UserSection): void {
  const ownerId = ownerSection?.userId ?? user.value?.id ?? null
  const ownerUsername = ownerSection?.username ?? user.value?.username ?? null
  const blank: AnimeListEntry = {
    sn: 0,
    enabled: true,
    bilingual: false,
    mode: null,
    tag: '',
    season: 1,
    custom_name: null,
    comment: '',
    anime_name: null,
    downloaded_episodes: 0,
    known_episodes: 0,
    owner_id: ownerId,
    owner_username: ownerUsername,
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

/** Unique collapse key for a (userSection, tag) combo. */
function sectionTagKey(userId: string, tag: string): string {
  return `${userId}::${tag}`
}

/** Tooltip text for a duplicate row. */
function duplicateTooltip(row: AnimeListEntry): string {
  if (!row.duplicate_of_entry_id) return ''
  const owner = row.duplicate_of_owner_username ?? '其他用戶'
  const name = row.duplicate_of_bangumi_name ?? '同名番劇'
  return `已停用：與 ${owner} 的「${name}」重複`
}

/** Handle toggle attempt on a duplicate row. */
function onDuplicateToggleAttempt(row: AnimeListEntry): void {
  if (row.duplicate_of_entry_id != null) {
    // Snap back — keep it disabled.
    row.enabled = false
    ElMessage.warning({ message: '與現有項目重複，無法啟用' })
  }
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
        @click="addEntry()"
      >
        新增項目
      </el-button>
      <el-button @click="extensionDialogOpen = true">
        <el-icon><Grid /></el-icon>
        瀏覽器擴充
      </el-button>
    </div>

    <!-- Initial-load skeleton — avoids a blank content area while the
         first fetch is in flight (subsequent reloads keep existing rows
         visible instead of flashing back to this). -->
    <el-skeleton
      v-if="loading && !hasLoadedOnce"
      :rows="6"
      animated
      class="ag-section"
    />

    <!-- ====== Unified grouped-by-user view (admin + non-admin) ====== -->
    <template v-else>
      <div
        v-for="section in allSections"
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
          <!-- Add button only in own section for non-admin; admin sees it on any section -->
          <el-button
            v-if="isAdmin || section.isSelf"
            size="small"
            class="ag-section-add-btn"
            @click="addEntry(section)"
          >
            ＋
          </el-button>
        </div>

        <!-- Tag sub-groups within this user -->
        <el-collapse
          v-model="activeSections"
          class="ag-section"
        >
          <el-collapse-item
            v-for="group in section.tagGroups"
            :key="sectionTagKey(section.userId, group.tag)"
            :name="sectionTagKey(section.userId, group.tag)"
            :title="`${groupLabel(group.tag)}（${group.rows.length}）`"
          >
            <el-table
              v-if="!isMobile"
              :data="group.rows"
              stripe
              size="small"
              class="ag-anime-table"
            >
              <!-- Enable toggle -->
              <el-table-column
                label="啟用"
                width="70"
              >
                <template #default="{ row }">
                  <!-- Duplicate: show disabled toggle with tooltip -->
                  <el-tooltip
                    v-if="row.duplicate_of_entry_id != null"
                    :content="duplicateTooltip(row)"
                    placement="top"
                  >
                    <el-switch
                      :model-value="false"
                      disabled
                      @change="onDuplicateToggleAttempt(row)"
                    />
                  </el-tooltip>
                  <!-- Readonly row (not own): disabled toggle -->
                  <el-switch
                    v-else-if="!isOwnRow(row)"
                    :model-value="row.enabled"
                    disabled
                  />
                  <!-- Own row: fully interactive -->
                  <el-switch
                    v-else
                    v-model="row.enabled"
                  />
                </template>
              </el-table-column>

              <!-- Bilingual toggle -->
              <el-table-column
                label="雙語"
                width="70"
              >
                <template #header>
                  <el-tooltip
                    content="同時抓日文原音與中文配音（中文配音會加上 [中] 檔名標記）"
                    placement="top"
                  >
                    <span>雙語</span>
                  </el-tooltip>
                </template>
                <template #default="{ row }">
                  <el-tooltip
                    v-if="row.duplicate_of_entry_id != null"
                    :content="duplicateTooltip(row)"
                    placement="top"
                  >
                    <el-switch
                      :model-value="row.bilingual"
                      disabled
                    />
                  </el-tooltip>
                  <el-switch
                    v-else-if="!isOwnRow(row)"
                    :model-value="row.bilingual"
                    disabled
                  />
                  <el-switch
                    v-else
                    v-model="row.bilingual"
                  />
                </template>
              </el-table-column>

              <!-- Warning icon for duplicates -->
              <el-table-column
                width="30"
              >
                <template #default="{ row }">
                  <el-tooltip
                    v-if="row.duplicate_of_entry_id != null"
                    :content="duplicateTooltip(row)"
                    placement="top"
                  >
                    <span class="ag-dup-icon">⚠</span>
                  </el-tooltip>
                </template>
              </el-table-column>

              <!-- sn -->
              <el-table-column
                label="sn"
                width="100"
              >
                <template #default="{ row }">
                  <el-input-number
                    v-model="row.sn"
                    :min="0"
                    :controls="false"
                    :disabled="!isOwnRow(row)"
                    size="small"
                    class="ag-sn-input"
                  />
                </template>
              </el-table-column>

              <!-- Anime name -->
              <el-table-column
                label="番劇名稱"
                min-width="180"
              >
                <template #default="{ row }">
                  <el-tooltip
                    v-if="row.anime_name"
                    :content="row.anime_name"
                    placement="top"
                    :show-after="300"
                  >
                    <span class="ag-truncate">{{ row.anime_name }}</span>
                  </el-tooltip>
                  <span
                    v-else
                    class="ag-muted"
                  >（尚未下載）</span>
                </template>
              </el-table-column>

              <!-- Custom name -->
              <el-table-column
                label="自訂名稱"
                width="160"
              >
                <template #default="{ row }">
                  <el-input
                    :model-value="getCustomNameValue(row)"
                    placeholder="（預設使用抓到的名稱）"
                    :disabled="!isOwnRow(row)"
                    size="small"
                    @update:model-value="setCustomNameDraft(row, $event)"
                    @blur="commitCustomNameDraft(row)"
                    @keyup.enter="commitCustomNameDraft(row)"
                  />
                </template>
              </el-table-column>

              <!-- Download mode -->
              <el-table-column
                label="下載模式"
                width="160"
              >
                <template #default="{ row }">
                  <el-select
                    v-model="row.mode"
                    placeholder="使用預設"
                    clearable
                    :disabled="!isOwnRow(row)"
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

              <!-- Season -->
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
                    :disabled="!isOwnRow(row)"
                    size="small"
                    class="ag-season-input"
                  />
                </template>
              </el-table-column>

              <!-- Comment -->
              <el-table-column
                label="註釋"
                min-width="140"
              >
                <template #default="{ row }">
                  <el-input
                    v-model="row.comment"
                    placeholder="（可空）"
                    :disabled="!isOwnRow(row)"
                    size="small"
                  />
                </template>
              </el-table-column>

              <!-- Episode count -->
              <el-table-column
                label="集數"
                width="90"
              >
                <template #default="{ row }">
                  <span class="ag-episode">{{ episodeText(row) }}</span>
                </template>
              </el-table-column>

              <!-- Tag group -->
              <el-table-column
                label="群組"
                width="140"
              >
                <template #default="{ row }">
                  <el-input
                    :model-value="getTagValue(row)"
                    placeholder="（未分類）"
                    :disabled="!isOwnRow(row)"
                    size="small"
                    @update:model-value="setTagDraft(row, $event)"
                    @blur="commitTagDraft(row)"
                    @keyup.enter="commitTagDraft(row)"
                  />
                </template>
              </el-table-column>

              <!-- Actions: delete only for own rows -->
              <el-table-column
                label="操作"
                width="80"
              >
                <template #default="{ row }">
                  <el-button
                    v-if="isOwnRow(row)"
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

            <!-- Mobile: hide the table, render each row as a stacked card -->
            <div
              v-else
              class="ag-anime-cards"
            >
              <div
                v-for="(row, idx) in group.rows"
                :key="idx"
                class="ag-anime-card"
              >
                <div class="ag-anime-card__header">
                  <el-tooltip
                    v-if="row.anime_name"
                    :content="row.anime_name"
                    placement="top"
                    :show-after="300"
                  >
                    <span class="ag-anime-card__name">{{ row.anime_name }}</span>
                  </el-tooltip>
                  <span
                    v-else
                    class="ag-anime-card__name ag-muted"
                  >（尚未下載）</span>
                  <el-tooltip
                    v-if="row.duplicate_of_entry_id != null"
                    :content="duplicateTooltip(row)"
                    placement="top"
                  >
                    <span class="ag-dup-icon">⚠</span>
                  </el-tooltip>
                </div>

                <div class="ag-anime-card__switches">
                  <label class="ag-anime-card__switch-field">
                    <span class="ag-anime-card__label">啟用</span>
                    <el-tooltip
                      v-if="row.duplicate_of_entry_id != null"
                      :content="duplicateTooltip(row)"
                      placement="top"
                    >
                      <el-switch
                        :model-value="false"
                        disabled
                        @change="onDuplicateToggleAttempt(row)"
                      />
                    </el-tooltip>
                    <el-switch
                      v-else-if="!isOwnRow(row)"
                      :model-value="row.enabled"
                      disabled
                    />
                    <el-switch
                      v-else
                      v-model="row.enabled"
                    />
                  </label>
                  <label class="ag-anime-card__switch-field">
                    <span class="ag-anime-card__label">雙語</span>
                    <el-tooltip
                      v-if="row.duplicate_of_entry_id != null"
                      :content="duplicateTooltip(row)"
                      placement="top"
                    >
                      <el-switch
                        :model-value="row.bilingual"
                        disabled
                      />
                    </el-tooltip>
                    <el-switch
                      v-else-if="!isOwnRow(row)"
                      :model-value="row.bilingual"
                      disabled
                    />
                    <el-switch
                      v-else
                      v-model="row.bilingual"
                    />
                  </label>
                </div>

                <div class="ag-anime-card__field">
                  <span class="ag-anime-card__label">sn</span>
                  <el-input-number
                    v-model="row.sn"
                    :min="0"
                    :controls="false"
                    :disabled="!isOwnRow(row)"
                    size="small"
                    class="ag-anime-card__control"
                  />
                </div>

                <div class="ag-anime-card__field">
                  <span class="ag-anime-card__label">自訂名稱</span>
                  <el-input
                    :model-value="getCustomNameValue(row)"
                    placeholder="（預設使用抓到的名稱）"
                    :disabled="!isOwnRow(row)"
                    size="small"
                    class="ag-anime-card__control"
                    @update:model-value="setCustomNameDraft(row, $event)"
                    @blur="commitCustomNameDraft(row)"
                    @keyup.enter="commitCustomNameDraft(row)"
                  />
                </div>

                <div class="ag-anime-card__field">
                  <span class="ag-anime-card__label">下載模式</span>
                  <el-select
                    v-model="row.mode"
                    placeholder="使用預設"
                    clearable
                    :disabled="!isOwnRow(row)"
                    size="small"
                    class="ag-anime-card__control"
                  >
                    <el-option
                      v-for="m in MODES"
                      :key="m.value"
                      :label="m.label"
                      :value="m.value"
                    />
                  </el-select>
                </div>

                <div class="ag-anime-card__row">
                  <div class="ag-anime-card__field ag-anime-card__field--inline">
                    <span class="ag-anime-card__label">季</span>
                    <el-input-number
                      v-model="row.season"
                      :min="1"
                      :step="1"
                      :controls="false"
                      :disabled="!isOwnRow(row)"
                      size="small"
                      class="ag-anime-card__control"
                    />
                  </div>
                  <div class="ag-anime-card__field ag-anime-card__field--inline">
                    <span class="ag-anime-card__label">集數</span>
                    <span class="ag-episode">{{ episodeText(row) }}</span>
                  </div>
                </div>

                <div class="ag-anime-card__field">
                  <span class="ag-anime-card__label">註釋</span>
                  <el-input
                    v-model="row.comment"
                    placeholder="（可空）"
                    :disabled="!isOwnRow(row)"
                    size="small"
                    class="ag-anime-card__control"
                  />
                </div>

                <div class="ag-anime-card__field">
                  <span class="ag-anime-card__label">群組</span>
                  <el-input
                    :model-value="getTagValue(row)"
                    placeholder="（未分類）"
                    :disabled="!isOwnRow(row)"
                    size="small"
                    class="ag-anime-card__control"
                    @update:model-value="setTagDraft(row, $event)"
                    @blur="commitTagDraft(row)"
                    @keyup.enter="commitTagDraft(row)"
                  />
                </div>

                <div
                  v-if="isOwnRow(row)"
                  class="ag-anime-card__footer"
                >
                  <el-button
                    size="small"
                    type="danger"
                    link
                    @click="removeEntry(row)"
                  >
                    刪除
                  </el-button>
                </div>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>

      <div
        v-if="entries.length === 0"
        class="ag-empty"
      >
        目前追番清單為空，點擊上方「新增項目」開始追蹤。
      </div>
    </template>

    <DirtyFab
      :visible="dirty"
      :saving="saving"
      @save="save"
      @discard="discard"
    />

    <BrowserExtensionDialog v-model="extensionDialogOpen" />
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
  flex-wrap: wrap;
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
.ag-truncate {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
}
.ag-episode {
  font-variant-numeric: tabular-nums;
}
.ag-empty {
  text-align: center;
  color: #9ca3af;
  padding: 32px 0;
}
.ag-dup-icon {
  color: #e6a23c;
  font-size: 14px;
  cursor: default;
}

/* User-section styles */
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
.ag-section-add-btn {
  margin-left: 8px;
}

/* Readonly rows: slightly muted to signal non-editability */
:deep(.ag-readonly-row) {
  background-color: rgba(0, 0, 0, 0.02);
  opacity: 0.92;
}

/* ---------------------------------------------------------------------
   Mobile card mode — one card per row, replacing the (unusable-at-375px)
   el-table. Field labels are laid out to the left of each control so the
   card reads like a compact form rather than a shrunken table.
   --------------------------------------------------------------------- */
.ag-anime-cards {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.ag-anime-card {
  border: 1px solid var(--el-border-color, #e4e7ed);
  border-radius: 8px;
  padding: 12px;
  background: var(--el-bg-color, #fff);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.ag-anime-card__header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.ag-anime-card__name {
  font-weight: 600;
  font-size: 15px;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ag-anime-card__switches {
  display: flex;
  gap: 20px;
}
.ag-anime-card__switch-field {
  display: flex;
  align-items: center;
  gap: 8px;
}
.ag-anime-card__field {
  display: flex;
  align-items: center;
  gap: 10px;
}
.ag-anime-card__field--inline {
  flex: 1;
  min-width: 0;
}
.ag-anime-card__row {
  display: flex;
  gap: 16px;
}
.ag-anime-card__label {
  flex-shrink: 0;
  width: 64px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.ag-anime-card__control {
  flex: 1;
  min-width: 0;
}
.ag-anime-card__footer {
  display: flex;
  justify-content: flex-end;
  border-top: 1px solid var(--el-border-color-lighter, #ebeef5);
  padding-top: 8px;
}
</style>
