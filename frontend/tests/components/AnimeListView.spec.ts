import { describe, expect, it, vi } from 'vitest'
import type { AnimeListEntry, AnimeListPayload } from '@/types'

const list = vi.fn()
const replaceAll = vi.fn().mockResolvedValue({ status: 'ok' })

vi.mock('@/api/animelist', () => ({
  AnimeListApi: class {
    list = list
    replaceAll = replaceAll
  },
}))

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))

import { flushPromises, mount } from '@vue/test-utils'
import AnimeListView from '@/views/AnimeListView.vue'
import { ElMessage } from 'element-plus'
// Canonical Element Plus stub set — shared with MonitorView /
// ManualTaskDialog specs via tests/helpers/elementPlusStubs.ts.
import { createElementPlusStubs } from '../helpers/elementPlusStubs'

const stubs = createElementPlusStubs()

function makeEntry(overrides: Partial<AnimeListEntry> = {}): AnimeListEntry {
  return {
    sn: 0,
    enabled: true,
    mode: null,
    tag: '',
    season: 1,
    comment: '',
    anime_name: null,
    downloaded_episodes: 0,
    known_episodes: 0,
    ...overrides,
  }
}

function payload(entries: AnimeListEntry[]): AnimeListPayload {
  return { entries }
}

describe('AnimeListView', () => {
  it('loads entries on mount and renders them grouped by tag', async () => {
    list.mockResolvedValueOnce(
      payload([
        makeEntry({ sn: 111, tag: '本季新番', anime_name: '範例番劇 A', known_episodes: 12, downloaded_episodes: 3 }),
        makeEntry({ sn: 222, tag: '本季新番', anime_name: '範例番劇 B', known_episodes: 8, downloaded_episodes: 8 }),
        makeEntry({ sn: 333, tag: '', anime_name: '未分類番劇' }),
      ]),
    )
    const wrapper = mount(AnimeListView, { global: { stubs } })
    await flushPromises()

    expect(list).toHaveBeenCalled()

    const groups = wrapper.findAll('.el-collapse-item')
    expect(groups).toHaveLength(2)
    const titles = groups.map((g) => g.find('.el-collapse-title').text())
    expect(titles.some((t) => t.includes('本季新番') && t.includes('2'))).toBe(true)
    expect(titles.some((t) => t.includes('未分類') && t.includes('1'))).toBe(true)

    const text = wrapper.text()
    expect(text).toContain('範例番劇 A')
    expect(text).toContain('範例番劇 B')
    expect(text).toContain('未分類番劇')

    // Fab is hidden while nothing is dirty, so no 儲存 button exists.
    const saveBtn = wrapper.findAll('button').find((b) => b.text().trim() === '儲存')
    expect(saveBtn).toBeUndefined()
    expect(wrapper.text()).not.toContain('尚未儲存')
  })

  it('shows "—" when the backend returns anime_name: null with zero episode counts', async () => {
    list.mockResolvedValueOnce(
      payload([makeEntry({ sn: 777, anime_name: null, downloaded_episodes: 0, known_episodes: 0 })]),
    )
    const wrapper = mount(AnimeListView, { global: { stubs } })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('—')
    expect(text).toContain('（尚未下載）')
  })

  it('toggling 啟用 marks dirty; 儲存 calls PUT with the mutated entries', async () => {
    list.mockReset()
    list.mockResolvedValue(
      payload([
        makeEntry({ sn: 111, enabled: true, tag: '群組甲' }),
        makeEntry({ sn: 222, enabled: true, tag: '群組乙' }),
      ]),
    )
    replaceAll.mockClear()
    const wrapper = mount(AnimeListView, { global: { stubs } })
    await flushPromises()

    // Initially clean → fab hidden, no 儲存 button yet.
    expect(
      wrapper.findAll('button').find((b) => b.text().trim() === '儲存'),
    ).toBeUndefined()

    // Toggle the first switch (啟用 column of the first row).
    const firstSwitch = wrapper.find('input.el-switch')
    await firstSwitch.setValue(false)

    // Dirty badge should now appear (inside the DirtyFab).
    expect(wrapper.text()).toContain('尚未儲存')
    const saveBtn = wrapper.findAll('button').find((b) => b.text().trim() === '儲存')!
    expect(saveBtn.attributes('disabled')).toBeUndefined()

    // Re-stub list for the post-save reload.
    list.mockResolvedValue(
      payload([
        makeEntry({ sn: 111, enabled: false, tag: '群組甲' }),
        makeEntry({ sn: 222, enabled: true, tag: '群組乙' }),
      ]),
    )

    await saveBtn.trigger('click')
    await flushPromises()

    expect(replaceAll).toHaveBeenCalledTimes(1)
    const [sentEntries] = replaceAll.mock.calls[0]!
    expect(sentEntries).toHaveLength(2)
    const byS: Record<number, AnimeListEntry> = Object.fromEntries(
      (sentEntries as AnimeListEntry[]).map((e) => [e.sn, e]),
    )
    expect(byS[111].enabled).toBe(false)
    expect(byS[222].enabled).toBe(true)
    expect(ElMessage.success).toHaveBeenCalledWith('追番清單已儲存')
  })

  it('adds a blank row when 新增項目 is clicked', async () => {
    list.mockResolvedValueOnce(payload([makeEntry({ sn: 999, tag: '既有' })]))
    const wrapper = mount(AnimeListView, { global: { stubs } })
    await flushPromises()

    const initialRows = wrapper.findAll('.el-table-row').length
    expect(initialRows).toBe(1)

    const addBtn = wrapper.findAll('button').find((b) => b.text().trim() === '新增項目')!
    await addBtn.trigger('click')
    await flushPromises()

    const rowsAfter = wrapper.findAll('.el-table-row')
    expect(rowsAfter.length).toBe(initialRows + 1)
    // The new blank row lives in the 未分類 group.
    const titles = wrapper.findAll('.el-collapse-title').map((t) => t.text())
    expect(titles.some((t) => t.includes('未分類'))).toBe(true)
    // And the save button is now enabled (dirty).
    const saveBtn = wrapper.findAll('button').find((b) => b.text().trim() === '儲存')!
    expect(saveBtn.attributes('disabled')).toBeUndefined()
  })

  it('auto-expands a newly-typed tag group without collapsing others', async () => {
    list.mockResolvedValueOnce(
      payload([
        makeEntry({ sn: 111, tag: '既有', anime_name: 'A' }),
        makeEntry({ sn: 222, tag: '既有', anime_name: 'B' }),
      ]),
    )
    const wrapper = mount(AnimeListView, { global: { stubs } })
    await flushPromises()

    // Initial state: one group, expanded.
    const vm = wrapper.vm as unknown as {
      entries: AnimeListEntry[]
      activeGroups: string[]
    }
    expect(vm.activeGroups).toEqual(['既有'])

    // Simulate the user retagging row #222 into a brand-new group. We
    // mutate the entry directly — the template's el-input #update:modelValue
    // handler does the same thing when a user types.
    vm.entries[1]!.tag = '新群組'
    await flushPromises()

    // The new key must be appended to activeGroups, and the existing
    // expanded group must still be there.
    expect(vm.activeGroups).toContain('新群組')
    expect(vm.activeGroups).toContain('既有')

    // DOM-level proof: both collapse titles render.
    const titles = wrapper.findAll('.el-collapse-title').map((t) => t.text())
    expect(titles.some((t) => t.includes('既有'))).toBe(true)
    expect(titles.some((t) => t.includes('新群組'))).toBe(true)
  })

  it('test_typing_in_tag_input_does_not_reflow_grouped_table_midway: input events do not mutate tag but blur commits draft', async () => {
    list.mockResolvedValueOnce(
      payload([makeEntry({ sn: 111, tag: '既有', anime_name: 'A' })]),
    )
    const wrapper = mount(AnimeListView, { global: { stubs } })
    await flushPromises()

    const vm = wrapper.vm as unknown as { entries: AnimeListEntry[] }

    // The tag input in the 群組 column.
    const tagInput = wrapper.find('td[data-label="群組"] input.el-input')
    expect(tagInput.exists()).toBe(true)

    // Simulate keystrokes via 'input' events — the stub emits
    // 'update:modelValue', which feeds setTagDraft. row.tag must stay unchanged.
    const inputEl = tagInput.element as HTMLInputElement
    inputEl.value = '新'
    await tagInput.trigger('input')
    expect(vm.entries[0]!.tag).toBe('既有')

    inputEl.value = '新群組'
    await tagInput.trigger('input')
    expect(vm.entries[0]!.tag).toBe('既有')

    // Now simulate the commit phase via blur.
    // The stub emits 'blur', which the template wires to @blur="commitTagDraft(row)".
    await tagInput.trigger('blur')
    expect(vm.entries[0]!.tag).toBe('新群組')
  })

  it('test_blur_commits_tag_draft: blur after update:modelValue writes draft to entry.tag', async () => {
    list.mockResolvedValueOnce(
      payload([makeEntry({ sn: 555, tag: '舊組', anime_name: 'Blur Test' })]),
    )
    const wrapper = mount(AnimeListView, { global: { stubs } })
    await flushPromises()

    const vm = wrapper.vm as unknown as { entries: AnimeListEntry[] }
    const tagInput = wrapper.find('td[data-label="群組"] input.el-input')
    expect(tagInput.exists()).toBe(true)

    // Set draft value via input event (update:modelValue).
    const inputEl = tagInput.element as HTMLInputElement
    inputEl.value = 'new-group'
    await tagInput.trigger('input')

    // Before blur — entry.tag untouched.
    expect(vm.entries[0]!.tag).toBe('舊組')

    // Blur commits draft.
    await tagInput.trigger('blur')
    expect(vm.entries[0]!.tag).toBe('new-group')
  })

  it('test_typed_chars_are_visible_in_input: component shows draft value while typing, not stale tag', async () => {
    list.mockResolvedValueOnce(
      payload([makeEntry({ sn: 777, tag: '舊', anime_name: 'Draft Test' })]),
    )
    const wrapper = mount(AnimeListView, { global: { stubs } })
    await flushPromises()

    const tagInput = wrapper.find('td[data-label="群組"] input.el-input')
    expect(tagInput.exists()).toBe(true)

    // Before any typing, input reflects the current tag.
    expect((tagInput.element as HTMLInputElement).value).toBe('舊')

    // Simulate typing 'd', 'r', 'a', 'f', 't' via individual input events.
    const inputEl = tagInput.element as HTMLInputElement
    inputEl.value = 'draft'
    await tagInput.trigger('input')
    await wrapper.vm.$nextTick()

    // The input element's value attribute (driven by :model-value -> getTagValue)
    // should now reflect the draft, not the stale tag '舊'.
    // The stub binds :value="modelValue" which reads getTagValue(row).
    expect((tagInput.element as HTMLInputElement).value).toBe('draft')
  })

  it('test_typing_in_tag_input_updates_displayed_value: reactive Map draft is visible immediately after update:modelValue', async () => {
    // Regression: WeakMap was not reactive so the input appeared frozen.
    // reactive(Map) makes get/set observable → re-render fires → draft shows.
    list.mockResolvedValueOnce(
      payload([makeEntry({ sn: 42, tag: 'OldTag', anime_name: 'ReactiveTest' })]),
    )
    const wrapper = mount(AnimeListView, { global: { stubs } })
    await flushPromises()

    const tagInput = wrapper.find('td[data-label="群組"] input.el-input')
    expect(tagInput.exists()).toBe(true)

    // Emit update:modelValue (what el-input does while typing).
    const inputEl = tagInput.element as HTMLInputElement
    inputEl.value = 'foo'
    await tagInput.trigger('input')
    await wrapper.vm.$nextTick()

    // The stub binds :value="modelValue" which reads getTagValue(row).
    // With WeakMap this would stay 'OldTag'; with reactive Map it is 'foo'.
    expect((tagInput.element as HTMLInputElement).value).toBe('foo')
  })

  it('does not re-expand a tag the user manually collapsed', async () => {
    list.mockResolvedValueOnce(
      payload([makeEntry({ sn: 111, tag: '既有', anime_name: 'A' })]),
    )
    const wrapper = mount(AnimeListView, { global: { stubs } })
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      entries: AnimeListEntry[]
      activeGroups: string[]
    }
    // User collapses the existing group.
    vm.activeGroups = []
    await flushPromises()

    // Mutating the existing row's tag does not add '既有' back — only
    // truly new tags trigger auto-expand.
    vm.entries[0]!.tag = '既有'
    await flushPromises()
    expect(vm.activeGroups).toEqual([])

    // But a genuinely new tag does appear.
    vm.entries[0]!.tag = '全新'
    await flushPromises()
    expect(vm.activeGroups).toContain('全新')
  })
})
