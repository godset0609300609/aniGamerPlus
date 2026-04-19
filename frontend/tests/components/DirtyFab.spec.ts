import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import DirtyFab from '@/components/DirtyFab.vue'
// Canonical Element Plus stub set — shared with the other component
// specs via tests/helpers/elementPlusStubs.ts.
import { createElementPlusStubs } from '../helpers/elementPlusStubs'

const stubs = createElementPlusStubs()

describe('DirtyFab', () => {
  it('renders nothing when visible is false', () => {
    const wrapper = mount(DirtyFab, {
      props: { visible: false },
      global: { stubs },
    })
    expect(wrapper.find('.ag-fab').exists()).toBe(false)
    expect(wrapper.find('.el-tag').exists()).toBe(false)
    expect(wrapper.findAll('button')).toHaveLength(0)
  })

  it('renders badge + both buttons when visible is true and not saving', () => {
    const wrapper = mount(DirtyFab, {
      props: { visible: true },
      global: { stubs },
    })
    expect(wrapper.find('.ag-fab').exists()).toBe(true)
    expect(wrapper.find('.el-tag').text()).toBe('尚未儲存')

    const buttons = wrapper.findAll('button')
    expect(buttons).toHaveLength(2)
    const labels = buttons.map((b) => b.text().trim())
    expect(labels).toContain('放棄變更')
    expect(labels).toContain('儲存')

    // Neither button is disabled in the idle (non-saving) state.
    for (const b of buttons) {
      expect(b.attributes('disabled')).toBeUndefined()
      expect(b.attributes('data-loading')).toBe('false')
    }
  })

  it('disables discard and marks save as loading while saving', () => {
    const wrapper = mount(DirtyFab, {
      props: { visible: true, saving: true },
      global: { stubs },
    })
    const discardBtn = wrapper.findAll('button').find((b) => b.text().trim() === '放棄變更')!
    const saveBtn = wrapper.findAll('button').find((b) => b.text().trim() === '儲存')!

    expect(discardBtn.attributes('disabled')).toBeDefined()
    // Save stays disabled while loading (real Element Plus behaviour),
    // but the key assertion is that loading state is on.
    expect(saveBtn.attributes('data-loading')).toBe('true')
  })

  it('emits @save when the save button is clicked', async () => {
    const wrapper = mount(DirtyFab, {
      props: { visible: true },
      global: { stubs },
    })
    const saveBtn = wrapper.findAll('button').find((b) => b.text().trim() === '儲存')!
    await saveBtn.trigger('click')
    expect(wrapper.emitted('save')).toHaveLength(1)
    expect(wrapper.emitted('discard')).toBeUndefined()
  })

  it('emits @discard when the discard button is clicked', async () => {
    const wrapper = mount(DirtyFab, {
      props: { visible: true },
      global: { stubs },
    })
    const discardBtn = wrapper.findAll('button').find((b) => b.text().trim() === '放棄變更')!
    await discardBtn.trigger('click')
    expect(wrapper.emitted('discard')).toHaveLength(1)
    expect(wrapper.emitted('save')).toBeUndefined()
  })

  it('respects custom saveLabel / discardLabel / badgeText props', () => {
    const wrapper = mount(DirtyFab, {
      props: {
        visible: true,
        saveLabel: '提交變更',
        discardLabel: '重載配置',
        badgeText: '有未保存變更',
      },
      global: { stubs },
    })
    expect(wrapper.find('.el-tag').text()).toBe('有未保存變更')
    const labels = wrapper.findAll('button').map((b) => b.text().trim())
    expect(labels).toContain('提交變更')
    expect(labels).toContain('重載配置')
  })
})
