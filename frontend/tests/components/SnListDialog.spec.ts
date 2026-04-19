/**
 * Unit tests for SnListDialog.vue — dialog open/close and submit behaviour.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createElementPlusStubs, elementPlusModuleMock } from '../helpers/elementPlusStubs'

// ---------------------------------------------------------------------------
// SnListApi stub
// ---------------------------------------------------------------------------
const mockLoad = vi.fn()
const mockSave = vi.fn()

vi.mock('@/api/snlist', () => ({
  SnListApi: vi.fn().mockImplementation(() => ({
    load: mockLoad,
    save: mockSave,
  })),
}))

// ---------------------------------------------------------------------------
// Element Plus imperatives
// ---------------------------------------------------------------------------
const { mockElMessageSuccess, mockElMessageError } = vi.hoisted(() => ({
  mockElMessageSuccess: vi.fn(),
  mockElMessageError: vi.fn(),
}))

vi.mock('element-plus', () =>
  elementPlusModuleMock({
    ElMessage: {
      success: mockElMessageSuccess,
      error: mockElMessageError,
      warning: vi.fn(),
      info: vi.fn(),
    },
  }),
)

import SnListDialog from '@/components/SnListDialog.vue'

const stubs = {
  ...createElementPlusStubs(),
  // ElDialog stub that renders its default slot and footer slot.
  ElDialog: {
    props: ['modelValue', 'title', 'width'],
    emits: ['update:modelValue'],
    template:
      '<div class="el-dialog"><slot /><div class="footer"><slot name="footer" /></div></div>',
  },
  ElInput: {
    props: ['modelValue', 'type', 'rows', 'placeholder'],
    emits: ['update:modelValue'],
    template:
      '<textarea class="el-input" :value="modelValue" ' +
      "@input=\"$emit('update:modelValue', ($event.target).value)\" />",
  },
}

function mountDialog(modelValue = false) {
  return mount(SnListDialog, {
    props: { modelValue },
    global: { stubs },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  mockLoad.mockResolvedValue('# sn_list content')
  mockSave.mockResolvedValue({ status: 'ok' })
})

describe('SnListDialog — rendering', () => {
  it('renders the dialog wrapper', () => {
    const wrapper = mountDialog()
    expect(wrapper.find('.el-dialog').exists()).toBe(true)
  })

  it('renders the textarea input', () => {
    const wrapper = mountDialog()
    expect(wrapper.find('textarea.el-input').exists()).toBe(true)
  })
})

describe('SnListDialog — watch: loads content when dialog opens', () => {
  it('calls api.load when modelValue changes from false to true', async () => {
    const wrapper = mountDialog(false)
    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    expect(mockLoad).toHaveBeenCalledTimes(1)
  })

  it('displays loaded content in the textarea', async () => {
    mockLoad.mockResolvedValue('line1\nline2')
    const wrapper = mountDialog(false)
    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    const textarea = wrapper.find('textarea.el-input')
    expect((textarea.element as HTMLTextAreaElement).value).toBe('line1\nline2')
  })

  it('shows error message when api.load rejects', async () => {
    mockLoad.mockRejectedValue(new Error('load failed'))
    const wrapper = mountDialog(false)
    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalledWith(expect.stringContaining('load failed'))
  })

  it('does NOT call api.load when dialog closes (modelValue → false)', async () => {
    // Start open.
    const wrapper = mountDialog(true)
    await flushPromises()
    const callsBefore = mockLoad.mock.calls.length

    // Close.
    await wrapper.setProps({ modelValue: false })
    await flushPromises()

    // load() count must not have changed.
    expect(mockLoad.mock.calls.length).toBe(callsBefore)
  })
})

describe('SnListDialog — submit()', () => {
  it('calls api.save with the textarea content', async () => {
    const wrapper = mountDialog(true)
    await flushPromises()

    // Set textarea content.
    const textarea = wrapper.find('textarea.el-input')
    await textarea.setValue('new content')

    // Click the submit button.
    const submitBtn = wrapper.findAll('button').find((b) => b.text().trim() === '提交')!
    await submitBtn.trigger('click')
    await flushPromises()

    expect(mockSave).toHaveBeenCalledWith('new content')
    expect(mockElMessageSuccess).toHaveBeenCalledWith('sn_list 已更新')
  })

  it('shows error message when api.save rejects', async () => {
    mockSave.mockRejectedValue(new Error('save error'))
    const wrapper = mountDialog(true)
    await flushPromises()

    const submitBtn = wrapper.findAll('button').find((b) => b.text().trim() === '提交')!
    await submitBtn.trigger('click')
    await flushPromises()

    expect(mockElMessageError).toHaveBeenCalledWith(expect.stringContaining('save error'))
  })
})
