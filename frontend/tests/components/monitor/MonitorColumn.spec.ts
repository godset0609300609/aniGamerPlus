import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import MonitorColumn from '@/components/monitor/MonitorColumn.vue'
import type { TaskProgressEntry } from '@/types'
import { createElementPlusStubs } from '../../helpers/elementPlusStubs'

const stubs = createElementPlusStubs({
  ElScrollbar: {
    props: ['maxHeight'],
    template: '<div class="el-scrollbar"><slot /></div>',
  },
  ElEmpty: {
    props: ['description'],
    template: '<div class="el-empty">{{ description }}</div>',
  },
  ElProgress: {
    props: ['percentage'],
    template: '<div class="el-progress">{{ percentage }}%</div>',
  },
  // TaskCard is a real component; keep it real so we can check its content.
})

let _snCounter = 1
function makeTask(filename: string, status = '正在下載'): TaskProgressEntry {
  return { sn: _snCounter++, rate: 50, status, filename }
}

function mountColumn(
  tasks: TaskProgressEntry[],
  title = '下載中',
  variant: 'success' | 'info' | 'danger' | 'primary' = 'success',
  dimmed = false,
) {
  return mount(MonitorColumn, {
    props: { title, variant, tasks, dimmed },
    global: { stubs },
  })
}

describe('MonitorColumn — title and count', () => {
  it('renders the title', () => {
    const wrapper = mountColumn([])
    expect(wrapper.text()).toContain('下載中')
  })

  it('shows count = 0 when no tasks', () => {
    const wrapper = mountColumn([])
    expect(wrapper.text()).toContain('0')
  })

  it('shows correct count when tasks are provided', () => {
    const tasks = [makeTask('a.mp4'), makeTask('b.mp4')]
    const wrapper = mountColumn(tasks)
    expect(wrapper.text()).toContain('2')
  })
})

describe('MonitorColumn — empty state', () => {
  it('shows empty state when no tasks', () => {
    const wrapper = mountColumn([], '等待中', 'info')
    expect(wrapper.text()).toContain('等待中 沒有任務')
  })

  it('hides empty state when tasks exist', () => {
    const wrapper = mountColumn([makeTask('ep.mp4')])
    expect(wrapper.find('.el-empty').exists()).toBe(false)
  })
})

describe('MonitorColumn — task list', () => {
  it('renders task cards for each task', () => {
    const tasks = [makeTask('ep1.mp4'), makeTask('ep2.mp4')]
    const wrapper = mountColumn(tasks)
    const text = wrapper.text()
    expect(text).toContain('ep1.mp4')
    expect(text).toContain('ep2.mp4')
  })
})

describe('MonitorColumn — dimmed state', () => {
  it('applies dimmed class when dimmed=true', () => {
    const wrapper = mountColumn([makeTask('ep.mp4')], '下載中', 'success', true)
    expect(wrapper.find('.monitor-column--dimmed').exists()).toBe(true)
  })

  it('does not apply dimmed class when dimmed=false', () => {
    const wrapper = mountColumn([makeTask('ep.mp4')], '下載中', 'success', false)
    expect(wrapper.find('.monitor-column--dimmed').exists()).toBe(false)
  })
})

describe('MonitorColumn — fixed header / scrollable body layout', () => {
  it('renders __header element', () => {
    const wrapper = mountColumn([])
    expect(wrapper.find('.monitor-column__header').exists()).toBe(true)
  })

  it('renders __body element', () => {
    const wrapper = mountColumn([])
    expect(wrapper.find('.monitor-column__body').exists()).toBe(true)
  })

  it('__header and __body are siblings (not nested)', () => {
    const wrapper = mountColumn([makeTask('ep.mp4')])
    const column = wrapper.find('.monitor-column')
    const header = column.find('.monitor-column__header')
    const body = column.find('.monitor-column__body')
    // Both exist and are direct children of the column root
    expect(header.exists()).toBe(true)
    expect(body.exists()).toBe(true)
    // __body must NOT be inside __header
    expect(header.find('.monitor-column__body').exists()).toBe(false)
    // __header must NOT be inside __body
    expect(body.find('.monitor-column__header').exists()).toBe(false)
  })

  it('__body contains task cards when tasks are provided', () => {
    const wrapper = mountColumn([makeTask('ep1.mp4'), makeTask('ep2.mp4')])
    const body = wrapper.find('.monitor-column__body')
    expect(body.text()).toContain('ep1.mp4')
    expect(body.text()).toContain('ep2.mp4')
  })

  it('__body contains empty state when no tasks', () => {
    const wrapper = mountColumn([], '等待中', 'info')
    const body = wrapper.find('.monitor-column__body')
    expect(body.find('.el-empty').exists()).toBe(true)
  })
})

describe('MonitorColumn — variant header styles', () => {
  it('applies success header class for success variant', () => {
    const wrapper = mountColumn([], '下載中', 'success')
    expect(wrapper.find('.monitor-column__header--success').exists()).toBe(true)
  })

  it('applies info header class for info variant', () => {
    const wrapper = mountColumn([], '等待中', 'info')
    expect(wrapper.find('.monitor-column__header--info').exists()).toBe(true)
  })

  it('applies danger header class for danger variant', () => {
    const wrapper = mountColumn([], '錯誤', 'danger')
    expect(wrapper.find('.monitor-column__header--danger').exists()).toBe(true)
  })

  it('applies primary header class for primary variant', () => {
    const wrapper = mountColumn([], '近期完成', 'primary')
    expect(wrapper.find('.monitor-column__header--primary').exists()).toBe(true)
  })
})
