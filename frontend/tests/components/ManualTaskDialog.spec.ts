import { describe, expect, it, vi } from 'vitest'
// Canonical Element Plus stub set + imperative-API mock payload — shared
// with the other component specs via tests/helpers/elementPlusStubs.ts.
import { elementPlusModuleMock } from '../helpers/elementPlusStubs'

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
  form: { link: string; resolution: string; mode: string; thread: number }
  submit: () => Promise<void>
  submitting: boolean
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
})
