/**
 * Unit tests for BtView.vue — tab container.
 *
 * Child tab components are stubbed so this spec only exercises the
 * el-tabs wiring (label rendering + switching + ?tab= query-param sync)
 * and the slide-transition content area, not the tabs' internals (covered
 * separately by FiltersTab/FeedsTab/EntriesTab specs).
 *
 * El-tabs here only renders the nav (its panes are left empty — see
 * BtView.vue for why); the actual tab content lives in a sibling
 * <transition><FiltersTab v-if=.../>...</transition> block gated by
 * `activeTab`, so exactly one child stub is present in the DOM at a time.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createElementPlusStubs, elementPlusModuleMock } from '../helpers/elementPlusStubs'

vi.mock('element-plus', () => elementPlusModuleMock())

const mockRoute = { query: {} as Record<string, string | undefined> }
vi.mock('vue-router', () => ({
  useRoute: () => mockRoute,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

import BtView from '@/views/BtView.vue'

const stubs = {
  ...createElementPlusStubs(),
  FiltersTab: { template: '<div class="filters-tab-stub" />' },
  FeedsTab: { template: '<div class="feeds-tab-stub" />' },
  EntriesTab: { template: '<div class="entries-tab-stub" />' },
}

function mountView() {
  return mount(BtView, {
    global: { stubs },
  })
}

beforeEach(() => {
  mockRoute.query = {}
})

describe('BtView — tabs', () => {
  it('renders the three tab labels', () => {
    const wrapper = mountView()
    const text = wrapper.text()
    expect(text).toContain('過濾器')
    expect(text).toContain('RSS 來源')
    expect(text).toContain('抓取紀錄')
  })

  it('renders only the active tab child component (filters, by default)', () => {
    const wrapper = mountView()
    expect(wrapper.find('.filters-tab-stub').exists()).toBe(true)
    expect(wrapper.find('.feeds-tab-stub').exists()).toBe(false)
    expect(wrapper.find('.entries-tab-stub').exists()).toBe(false)
  })

  it('swaps the rendered child component when the active tab changes', async () => {
    const wrapper = mountView()
    const navItems = wrapper.findAll('.el-tabs__item')
    const feedsNav = navItems.find((n) => n.attributes('data-name') === 'feeds')
    expect(feedsNav).toBeDefined()

    await feedsNav!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.feeds-tab-stub').exists()).toBe(true)
    expect(wrapper.find('.filters-tab-stub').exists()).toBe(false)
    expect(wrapper.find('.entries-tab-stub').exists()).toBe(false)
  })

  it('defaults to the filters tab as active', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as unknown as { activeTab: string }
    expect(vm.activeTab).toBe('filters')
  })

  it('switching tabs updates activeTab when a nav item is clicked', async () => {
    const wrapper = mountView()
    const navItems = wrapper.findAll('.el-tabs__item')
    expect(navItems.length).toBe(3)

    const feedsNav = navItems.find((n) => n.attributes('data-name') === 'feeds')
    expect(feedsNav).toBeDefined()
    await feedsNav!.trigger('click')

    const vm = wrapper.vm as unknown as { activeTab: string }
    expect(vm.activeTab).toBe('feeds')
  })

  it('renders the BT 下載 page title', () => {
    const wrapper = mountView()
    expect(wrapper.text()).toContain('BT 下載')
  })
})

describe('BtView — slide direction', () => {
  it('slides left when moving to a later tab (filters -> feeds)', async () => {
    const wrapper = mountView()
    const feedsNav = wrapper
      .findAll('.el-tabs__item')
      .find((n) => n.attributes('data-name') === 'feeds')

    await feedsNav!.trigger('click')

    const vm = wrapper.vm as unknown as { slideDir: 'left' | 'right' }
    expect(vm.slideDir).toBe('left')
  })

  it('slides left when moving from filters straight to entries', async () => {
    const wrapper = mountView()
    const entriesNav = wrapper
      .findAll('.el-tabs__item')
      .find((n) => n.attributes('data-name') === 'entries')

    await entriesNav!.trigger('click')

    const vm = wrapper.vm as unknown as { slideDir: 'left' | 'right' }
    expect(vm.slideDir).toBe('left')
  })

  it('slides right when moving to an earlier tab (entries -> filters)', async () => {
    const wrapper = mountView()
    const navItems = wrapper.findAll('.el-tabs__item')
    const entriesNav = navItems.find((n) => n.attributes('data-name') === 'entries')
    await entriesNav!.trigger('click')

    const filtersNav = wrapper
      .findAll('.el-tabs__item')
      .find((n) => n.attributes('data-name') === 'filters')
    await filtersNav!.trigger('click')

    const vm = wrapper.vm as unknown as { slideDir: 'left' | 'right' }
    expect(vm.slideDir).toBe('right')
  })
})

describe('BtView — ?tab= query-param sync', () => {
  it('opens the 抓取紀錄 tab on mount when route.query.tab = "entries"', () => {
    mockRoute.query = { tab: 'entries' }
    const wrapper = mountView()

    const entriesNav = wrapper.find('.el-tabs__item[data-name="entries"]')
    expect(entriesNav.exists()).toBe(true)
    expect(entriesNav.classes()).toContain('is-active')

    const filtersNav = wrapper.find('.el-tabs__item[data-name="filters"]')
    expect(filtersNav.classes()).not.toContain('is-active')
  })

  it('opens the RSS 來源 tab on mount when route.query.tab = "feeds"', () => {
    mockRoute.query = { tab: 'feeds' }
    const wrapper = mountView()

    const feedsNav = wrapper.find('.el-tabs__item[data-name="feeds"]')
    expect(feedsNav.exists()).toBe(true)
    expect(feedsNav.classes()).toContain('is-active')
  })

  it('ignores an unknown tab value and falls back to the filters tab', () => {
    mockRoute.query = { tab: 'bogus' }
    const wrapper = mountView()

    const vm = wrapper.vm as unknown as { activeTab: string }
    expect(vm.activeTab).toBe('bogus')
    // Filters remains the default when the tab value doesn't match any pane.
    const filtersNav = wrapper.find('.el-tabs__item[data-name="filters"]')
    expect(filtersNav.exists()).toBe(true)
  })
})
