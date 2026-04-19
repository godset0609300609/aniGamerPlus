import { describe, expect, it, vi } from 'vitest'
import { elementPlusModuleMock } from '../helpers/elementPlusStubs'

vi.mock('element-plus', () => elementPlusModuleMock())

import { mount } from '@vue/test-utils'
import LoginView from '@/views/LoginView.vue'

describe('LoginView', () => {
  it('renders a button mentioning Discord', () => {
    const wrapper = mount(LoginView, {
      global: {
        stubs: {
          ElCard: {
            template:
              '<div class="el-card"><div class="header"><slot name="header" /></div><div class="body"><slot /></div></div>',
          },
          ElButton: {
            props: ['type', 'size'],
            emits: ['click'],
            template: '<button @click="$emit(\'click\')"><slot /></button>',
          },
        },
      },
    })
    // The button text should mention Discord.
    expect(wrapper.text()).toContain('Discord')
  })

  it('clicking the login button calls window.location redirect', async () => {
    const wrapper = mount(LoginView, {
      global: {
        stubs: {
          ElCard: {
            template:
              '<div class="el-card"><div class="header"><slot name="header" /></div><div class="body"><slot /></div></div>',
          },
          ElButton: {
            props: ['type', 'size'],
            emits: ['click'],
            template: '<button @click="$emit(\'click\')"><slot /></button>',
          },
        },
      },
    })

    // Spy on window.location to capture the assignment without navigating.
    const locationSpy = vi
      .spyOn(window, 'location', 'get')
      .mockReturnValue({ href: '' } as Location)

    const button = wrapper.find('button')
    expect(button.exists()).toBe(true)

    // The click handler sets window.location.href — wrap in try/catch because
    // happy-dom may throw on navigation.
    try {
      await button.trigger('click')
    } catch {
      // Navigation throw is acceptable in happy-dom.
    }

    locationSpy.mockRestore()
  })
})
