import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import MonitorHeader from '@/components/monitor/MonitorHeader.vue'
import { createElementPlusStubs } from '../../helpers/elementPlusStubs'

const stubs = createElementPlusStubs()

function mountHeader(
  counts = { downloading: 0, waiting: 0, completed: 0 },
  connectionState: 'connecting' | 'open' | 'closed' = 'open',
  showDisconnectedBanner = false,
  viewMode: 'table' | 'kanban' = 'kanban',
  isMobile = false,
) {
  return mount(MonitorHeader, {
    props: { counts, connectionState, showDisconnectedBanner, viewMode, isMobile },
    global: { stubs },
  })
}

describe('MonitorHeader — title', () => {
  it('renders the 任務監控 title', () => {
    const wrapper = mountHeader()
    expect(wrapper.text()).toContain('任務監控')
  })
})

describe('MonitorHeader — badges', () => {
  it('shows correct downloading count', () => {
    const wrapper = mountHeader({ downloading: 5, waiting: 2, completed: 1 })
    const text = wrapper.text()
    expect(text).toContain('下載中 5')
  })

  it('shows correct waiting count', () => {
    const wrapper = mountHeader({ downloading: 0, waiting: 3, completed: 0 })
    expect(wrapper.text()).toContain('等待中 3')
  })

  it('shows correct completed count', () => {
    const wrapper = mountHeader({ downloading: 0, waiting: 0, completed: 4 })
    expect(wrapper.text()).toContain('近期完成 4')
  })

  it('shows all counts as 0 by default', () => {
    const wrapper = mountHeader()
    expect(wrapper.text()).toContain('下載中 0')
    expect(wrapper.text()).toContain('等待中 0')
    expect(wrapper.text()).toContain('近期完成 0')
  })

  it('does not show 錯誤 badge', () => {
    const wrapper = mountHeader({ downloading: 1, waiting: 1, completed: 1 })
    expect(wrapper.text()).not.toContain('錯誤')
  })
})

describe('MonitorHeader — connection dot', () => {
  it('applies open class when state is open', () => {
    const wrapper = mountHeader({ downloading: 0, waiting: 0, completed: 0 }, 'open', false)
    expect(wrapper.find('.monitor-header__dot--open').exists()).toBe(true)
    expect(wrapper.find('.monitor-header__dot--connecting').exists()).toBe(false)
    expect(wrapper.find('.monitor-header__dot--closed').exists()).toBe(false)
  })

  it('applies connecting class when state is connecting', () => {
    const wrapper = mountHeader({ downloading: 0, waiting: 0, completed: 0 }, 'connecting', false)
    expect(wrapper.find('.monitor-header__dot--connecting').exists()).toBe(true)
    expect(wrapper.find('.monitor-header__dot--open').exists()).toBe(false)
  })

  it('applies closed class when state is closed and banner is shown', () => {
    const wrapper = mountHeader({ downloading: 0, waiting: 0, completed: 0 }, 'closed', true)
    expect(wrapper.find('.monitor-header__dot--closed').exists()).toBe(true)
    expect(wrapper.find('.monitor-header__dot--open').exists()).toBe(false)
  })

  it('does not apply closed class when state is closed but banner is hidden', () => {
    const wrapper = mountHeader({ downloading: 0, waiting: 0, completed: 0 }, 'closed', false)
    expect(wrapper.find('.monitor-header__dot--closed').exists()).toBe(false)
  })
})

describe('MonitorHeader — view mode toggle', () => {
  it('renders the table and kanban toggle labels', () => {
    const wrapper = mountHeader()
    expect(wrapper.text()).toContain('表格')
    expect(wrapper.text()).toContain('看板')
  })

  it('emits update:viewMode with "table" when the 表格 button is clicked', async () => {
    const wrapper = mountHeader({ downloading: 0, waiting: 0, completed: 0 }, 'open', false, 'kanban')
    const buttons = wrapper.findAll('.el-radio-button')
    const tableButton = buttons.find((b) => b.text().includes('表格'))
    expect(tableButton).toBeTruthy()
    await tableButton!.trigger('click')

    expect(wrapper.emitted('update:viewMode')).toBeTruthy()
    expect(wrapper.emitted('update:viewMode')![0]).toEqual(['table'])
  })

  it('hides the toggle entirely when isMobile is true', () => {
    const wrapper = mountHeader({ downloading: 0, waiting: 0, completed: 0 }, 'open', false, 'kanban', true)
    expect(wrapper.find('.monitor-header__view-toggle').exists()).toBe(false)
    expect(wrapper.findAll('.el-radio-button').length).toBe(0)
  })

  it('shows the toggle when isMobile is false (default)', () => {
    const wrapper = mountHeader()
    expect(wrapper.find('.monitor-header__view-toggle').exists()).toBe(true)
  })
})
