import { describe, expect, it, vi } from 'vitest'
// Canonical Element Plus stub set + imperative-API mock payload — shared
// with the other component specs via tests/helpers/elementPlusStubs.ts.
import { createElementPlusStubs, elementPlusModuleMock } from '../helpers/elementPlusStubs'

vi.mock('element-plus', () => elementPlusModuleMock())

// Patch the tasks/config api modules *before* we import the component.
const submitManual = vi.fn().mockResolvedValue({ status: 'ok' })
const load = vi.fn().mockResolvedValue({ 'multi-thread': 1 })

vi.mock('@/api/tasks', async () => {
  const actual = await vi.importActual<typeof import('@/api/tasks')>('@/api/tasks')
  return {
    ...actual,
    TasksApi: class {
      submitManual = submitManual
    },
  }
})
vi.mock('@/api/config', async () => {
  const actual = await vi.importActual<typeof import('@/api/config')>('@/api/config')
  return {
    ...actual,
    ConfigApi: class {
      load = load
    },
  }
})

import { mount } from '@vue/test-utils'
import ManualTaskDialog from '@/components/ManualTaskDialog.vue'
import { ElMessage } from 'element-plus'

type DialogVm = {
  form: {
    link: string
    resolution: string
    mode: string
    thread: number
    danmu: boolean
    bilingual: boolean
  }
  source: 'animad' | 'bilibili' | null
  submit: () => Promise<void>
  submitting: boolean
}

const stubs = {
  ...createElementPlusStubs(),
  ElFormItem: {
    props: ['label'],
    template: '<div class="el-form-item" :data-label="label"><slot /></div>',
  },
}

describe('ManualTaskDialog', () => {
  it('emits update:modelValue on successful submit', async () => {
    const wrapper = mount(ManualTaskDialog, {
      props: { modelValue: true },
    })

    // Reach into the component's reactive form and fill it.
    const vm = wrapper.vm as unknown as DialogVm
    vm.form.link = 'https://ani.gamer.com.tw/animeVideo.php?sn=42'
    vm.form.resolution = '720'
    vm.form.mode = 'single'
    vm.form.thread = 2

    await vm.submit()

    expect(submitManual).toHaveBeenCalledWith({
      sn: '42',
      resolution: '720',
      mode: 'single',
      thread: 2,
      classify: true,
      danmu: false,
      bilingual: false,
    })
    expect(wrapper.emitted('update:modelValue')?.[0]?.[0]).toBe(false)
  })

  it('shows an error and does not submit when link is invalid', async () => {
    const errorSpy = ElMessage.error as unknown as ReturnType<typeof vi.fn>
    errorSpy.mockClear()
    submitManual.mockClear()

    const wrapper = mount(ManualTaskDialog, {
      props: { modelValue: true },
    })
    const vm = wrapper.vm as unknown as DialogVm
    vm.form.link = 'not-a-link'
    await vm.submit()

    expect(submitManual).not.toHaveBeenCalled()
    expect(errorSpy).toHaveBeenCalled()
  })

  it('sets submitting=false after successful submit', async () => {
    submitManual.mockResolvedValue({ status: 'ok' })

    const wrapper = mount(ManualTaskDialog, { props: { modelValue: true } })
    const vm = wrapper.vm as unknown as DialogVm
    vm.form.link = 'https://ani.gamer.com.tw/animeVideo.php?sn=10'

    await vm.submit()

    expect(vm.submitting).toBe(false)
  })

  it('sets submitting=false after a failed submit', async () => {
    submitManual.mockRejectedValue(new Error('network error'))

    const wrapper = mount(ManualTaskDialog, { props: { modelValue: true } })
    const vm = wrapper.vm as unknown as DialogVm
    vm.form.link = 'https://ani.gamer.com.tw/animeVideo.php?sn=11'

    await vm.submit()

    expect(vm.submitting).toBe(false)
    // Reset mock for subsequent tests.
    submitManual.mockResolvedValue({ status: 'ok' })
  })

  it('detects bilibili source when given a bilibili URL', async () => {
    const wrapper = mount(ManualTaskDialog, { props: { modelValue: true } })
    const vm = wrapper.vm as unknown as DialogVm
    vm.form.link = 'https://www.bilibili.com/video/BV1xx411c7mD'
    await wrapper.vm.$nextTick()

    expect(vm.source).toBe('bilibili')
  })

  it('sends bilibili request with source=bilibili, mode=single, thread=1, danmu=false', async () => {
    submitManual.mockClear()
    submitManual.mockResolvedValue({ status: 'ok' })

    const wrapper = mount(ManualTaskDialog, { props: { modelValue: true } })
    const vm = wrapper.vm as unknown as DialogVm
    vm.form.link = 'https://www.bilibili.com/video/BV1xx411c7mD'
    vm.form.resolution = '1080'
    vm.form.thread = 4

    await vm.submit()

    expect(submitManual).toHaveBeenCalledWith({
      sn: 'https://www.bilibili.com/video/BV1xx411c7mD',
      source: 'bilibili',
      resolution: '1080',
      mode: 'single',
      thread: 1,
      classify: true,
      danmu: false,
    })
  })

  it('sends bilibili request for bare BV id', async () => {
    submitManual.mockClear()
    submitManual.mockResolvedValue({ status: 'ok' })

    const wrapper = mount(ManualTaskDialog, { props: { modelValue: true } })
    const vm = wrapper.vm as unknown as DialogVm
    vm.form.link = 'BV1xx411c7mD'

    await vm.submit()

    expect(submitManual).toHaveBeenCalledWith(
      expect.objectContaining({ source: 'bilibili', sn: 'BV1xx411c7mD' }),
    )
  })

  it('empty link hides mode and danmu, shows resolution/classify/thread', async () => {
    const wrapper = mount(ManualTaskDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-label="下載模式"]').exists()).toBe(false)
    expect(wrapper.find('[data-label="下載彈幕"]').exists()).toBe(false)
    expect(wrapper.find('[data-label="雙語"]').exists()).toBe(false)
    expect(wrapper.find('[data-label="下載解析度"]').exists()).toBe(true)
    expect(wrapper.find('[data-label="建立番劇資料夾"]').exists()).toBe(true)
    expect(wrapper.find('[data-label="最大同時下載數"]').exists()).toBe(true)
  })

  it('animad URL shows mode and danmu', async () => {
    const wrapper = mount(ManualTaskDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    const vm = wrapper.vm as unknown as DialogVm
    vm.form.link = 'https://ani.gamer.com.tw/animeVideo.php?sn=42'
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-label="下載模式"]').exists()).toBe(true)
    expect(wrapper.find('[data-label="下載彈幕"]').exists()).toBe(true)
  })

  it('bilibili URL hides mode and danmu, shows bilibili hint, thread disabled', async () => {
    const wrapper = mount(ManualTaskDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    const vm = wrapper.vm as unknown as DialogVm
    vm.form.link = 'https://www.bilibili.com/video/BV1xx411c7mD'
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-label="下載模式"]').exists()).toBe(false)
    expect(wrapper.find('[data-label="下載彈幕"]').exists()).toBe(false)
    expect(wrapper.find('.bilibili-hint').exists()).toBe(true)
    expect(wrapper.find('input.el-input-number').attributes('disabled')).toBeDefined()
  })

  it('garbage input hides mode and danmu', async () => {
    const wrapper = mount(ManualTaskDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    const vm = wrapper.vm as unknown as DialogVm
    vm.form.link = 'not-a-valid-url-or-sn'
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-label="下載模式"]').exists()).toBe(false)
    expect(wrapper.find('[data-label="下載彈幕"]').exists()).toBe(false)
  })

  // -------------------------------------------------------------------------
  // 雙語 (bilingual) switch — animad-only, plumbed through to submitManual.
  // -------------------------------------------------------------------------

  it('bilingual switch is visible for animad', async () => {
    const wrapper = mount(ManualTaskDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    const vm = wrapper.vm as unknown as DialogVm
    vm.form.link = 'https://ani.gamer.com.tw/animeVideo.php?sn=42'
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-label="雙語"]').exists()).toBe(true)
  })

  it('bilingual switch is hidden for bilibili', async () => {
    const wrapper = mount(ManualTaskDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    const vm = wrapper.vm as unknown as DialogVm
    vm.form.link = 'https://www.bilibili.com/video/BV1xx411c7mD'
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-label="雙語"]').exists()).toBe(false)
  })

  it('bilingual switch defaults to false', () => {
    const wrapper = mount(ManualTaskDialog, {
      props: { modelValue: true },
    })
    const vm = wrapper.vm as unknown as DialogVm

    expect(vm.form.bilingual).toBe(false)
  })

  it('submit for animad includes bilingual: true when toggled on', async () => {
    submitManual.mockClear()
    submitManual.mockResolvedValue({ status: 'ok' })

    const wrapper = mount(ManualTaskDialog, { props: { modelValue: true } })
    const vm = wrapper.vm as unknown as DialogVm
    vm.form.link = 'https://ani.gamer.com.tw/animeVideo.php?sn=42'
    vm.form.bilingual = true

    await vm.submit()

    expect(submitManual).toHaveBeenCalledWith(
      expect.objectContaining({ sn: '42', bilingual: true }),
    )
  })

  it('submit for bilibili does not send bilingual: true', async () => {
    submitManual.mockClear()
    submitManual.mockResolvedValue({ status: 'ok' })

    const wrapper = mount(ManualTaskDialog, { props: { modelValue: true } })
    const vm = wrapper.vm as unknown as DialogVm
    vm.form.link = 'https://www.bilibili.com/video/BV1xx411c7mD'
    // Even if bilingual was left on from a prior animad selection, the
    // bilibili branch must never forward it as true.
    vm.form.bilingual = true

    await vm.submit()

    const payload = submitManual.mock.calls[0][0] as Record<string, unknown>
    expect(payload.bilingual).not.toBe(true)
  })
})
