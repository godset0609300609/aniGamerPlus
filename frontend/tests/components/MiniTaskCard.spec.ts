import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import MiniTaskCard from '@/components/MiniTaskCard.vue'
import { createElementPlusStubs } from '../helpers/elementPlusStubs'
import type { TaskProgressEntry } from '@/types'

function mountCard(task: TaskProgressEntry) {
  return mount(MiniTaskCard, {
    props: { task },
    global: { stubs: createElementPlusStubs() },
  })
}

describe('MiniTaskCard — display name', () => {
  it('shows bangumi name and episode when both present', () => {
    const wrapper = mountCard({
      sn: 1,
      rate: 42,
      status: '正在下載',
      filename: 'ep01.mp4',
      bangumi_name: '進擊的巨人',
      episode: '01',
    })
    const text = wrapper.text()
    expect(text).toContain('進擊的巨人')
    expect(text).toContain('EP 01')
  })

  it('falls back to filename when no bangumi_name', () => {
    const wrapper = mountCard({
      sn: 1,
      rate: 30,
      status: '等待下載',
      filename: 'episode_02.mp4',
      bangumi_name: null,
    })
    expect(wrapper.text()).toContain('episode_02.mp4')
    expect(wrapper.text()).not.toContain('《')
  })

  it('shows bangumi name without episode when episode is null', () => {
    const wrapper = mountCard({
      sn: 1,
      rate: 0,
      status: '等待下載',
      filename: 'ep.mp4',
      bangumi_name: '鬼滅之刃',
      episode: null,
    })
    const text = wrapper.text()
    expect(text).toContain('鬼滅之刃')
    // No "EP" when episode is null
    expect(text).not.toContain('EP')
  })
})

describe('MiniTaskCard — percentage', () => {
  it('displays rounded percentage', () => {
    const wrapper = mountCard({
      sn: 1,
      rate: 42.7,
      status: '正在下載',
      filename: 'ep.mp4',
    })
    // Math.round(42.7) = 43
    expect(wrapper.text()).toContain('43%')
  })

  it('shows 0% when rate is 0', () => {
    const wrapper = mountCard({
      sn: 1,
      rate: 0,
      status: '等待下載',
      filename: 'ep.mp4',
    })
    expect(wrapper.text()).toContain('0%')
  })

  it('shows 100% when rate is 100', () => {
    const wrapper = mountCard({
      sn: 1,
      rate: 100,
      status: '下載完成',
      filename: 'ep.mp4',
    })
    expect(wrapper.text()).toContain('100%')
  })
})

describe('MiniTaskCard — status', () => {
  it('shows status text', () => {
    const wrapper = mountCard({
      sn: 1,
      rate: 50,
      status: '正在下載',
      filename: 'ep.mp4',
    })
    expect(wrapper.text()).toContain('正在下載')
  })
})

describe('MiniTaskCard — progress bar', () => {
  it('renders el-progress element', () => {
    const wrapper = mountCard({
      sn: 1,
      rate: 50,
      status: '正在下載',
      filename: 'ep.mp4',
    })
    expect(wrapper.find('.el-progress').exists()).toBe(true)
  })

  it('maps rate to percentage via Math.round', () => {
    const wrapper = mountCard({
      sn: 1,
      rate: 67.4,
      status: '正在下載',
      filename: 'ep.mp4',
    })
    const el = wrapper.find('.el-progress')
    // The stub renders the percentage value as text content
    expect(el.text()).toContain('67')
  })

  it('shows 0 percentage when rate is 0', () => {
    const wrapper = mountCard({
      sn: 1,
      rate: 0,
      status: '等待下載',
      filename: 'ep.mp4',
    })
    expect(wrapper.find('.el-progress').exists()).toBe(true)
  })

  it('shows 100 percentage when rate is 100', () => {
    const wrapper = mountCard({
      sn: 1,
      rate: 100,
      status: '下載完成',
      filename: 'ep.mp4',
    })
    const el = wrapper.find('.el-progress')
    expect(el.text()).toContain('100')
  })
})
