import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { useBreakpoint } from '@/composables/useBreakpoint'

/**
 * Minimal stand-in for the browser's MediaQueryList — captures the
 * listener so a test can flip `matches` and fire it manually, mirroring
 * the pattern already established in tests/composables/useDarkMode.spec.ts.
 */
function makeMql(initialMatches: boolean) {
  let listener: (() => void) | null = null
  return {
    mql: {
      matches: initialMatches,
      addEventListener: (_: string, cb: () => void) => {
        listener = cb
      },
      removeEventListener: vi.fn(),
      dispatchEvent: () => true,
      onchange: null,
    },
    fire(matches: boolean) {
      // Mutate the object the composable already holds a reference to,
      // then invoke the captured listener — matches real MediaQueryList
      // semantics where `.matches` is read fresh on every change event.
      this.mql.matches = matches
      listener?.()
    },
  }
}

/** Mount the composable inside a real component so onMounted/onUnmounted fire. */
function mountBreakpoint() {
  let result!: ReturnType<typeof useBreakpoint>
  const Wrapper = defineComponent({
    setup() {
      result = useBreakpoint()
      return () => null
    },
  })
  const wrapper = mount(Wrapper)
  return { result, wrapper }
}

describe('useBreakpoint', () => {
  let mobile: ReturnType<typeof makeMql>
  let tablet: ReturnType<typeof makeMql>

  function stubMatchMedia(): void {
    vi.stubGlobal('matchMedia', (query: string) => (query.includes('min-width') ? tablet.mql : mobile.mql))
  }

  beforeEach(() => {
    mobile = makeMql(false)
    tablet = makeMql(false)
    stubMatchMedia()
  })

  it('defaults to desktop (isMobile/isTablet both false) when neither query matches', () => {
    const { result } = mountBreakpoint()
    expect(result.isMobile.value).toBe(false)
    expect(result.isTablet.value).toBe(false)
  })

  it('reports isMobile true when the mock reports the max-width:767px query matches', () => {
    mobile = makeMql(true)
    stubMatchMedia()

    const { result } = mountBreakpoint()
    expect(result.isMobile.value).toBe(true)
    expect(result.isTablet.value).toBe(false)
  })

  it('reports isTablet true when the mock reports the tablet range query matches', () => {
    tablet = makeMql(true)
    stubMatchMedia()

    const { result } = mountBreakpoint()
    expect(result.isTablet.value).toBe(true)
    expect(result.isMobile.value).toBe(false)
  })

  it('updates reactively when the mobile media query change event fires', async () => {
    const { result } = mountBreakpoint()
    expect(result.isMobile.value).toBe(false)

    mobile.fire(true)
    await null

    expect(result.isMobile.value).toBe(true)

    mobile.fire(false)
    await null

    expect(result.isMobile.value).toBe(false)
  })

  it('cleans up both listeners on unmount', () => {
    const { wrapper } = mountBreakpoint()
    wrapper.unmount()

    expect(mobile.mql.removeEventListener).toHaveBeenCalledWith('change', expect.any(Function))
    expect(tablet.mql.removeEventListener).toHaveBeenCalledWith('change', expect.any(Function))
  })
})
