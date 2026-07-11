import { onMounted, onUnmounted, ref, type Ref } from 'vue'

/**
 * Shared responsive breakpoints (px, max-width) — keep in sync with the
 * `--bp-mobile` / `--bp-tablet` custom properties declared in
 * `src/style.css` so JS-driven layout decisions (this composable) and
 * CSS-driven ones (`@media` queries) never disagree.
 */
export const MOBILE_MAX_WIDTH = 767
export const TABLET_MAX_WIDTH = 1023

const MOBILE_QUERY = `(max-width: ${MOBILE_MAX_WIDTH}px)`
const TABLET_QUERY = `(min-width: ${MOBILE_MAX_WIDTH + 1}px) and (max-width: ${TABLET_MAX_WIDTH}px)`

export interface Breakpoint {
  /** True at viewport widths <= 767px (phone). */
  isMobile: Ref<boolean>
  /** True at viewport widths 768px - 1023px (tablet). */
  isTablet: Ref<boolean>
}

/**
 * Reactive viewport breakpoint state, driven by `matchMedia` listeners
 * (rather than a `resize` handler) so updates fire exactly on the CSS
 * breakpoints above — no debouncing or width math needed at the call
 * site. Mirrors the mount/unmount lifecycle pattern used by
 * `useDarkMode`'s system-preference listener.
 */
export function useBreakpoint(): Breakpoint {
  const isMobile = ref(false)
  const isTablet = ref(false)

  const mqlMobile = typeof window !== 'undefined' ? (window.matchMedia?.(MOBILE_QUERY) ?? null) : null
  const mqlTablet = typeof window !== 'undefined' ? (window.matchMedia?.(TABLET_QUERY) ?? null) : null

  function update(): void {
    isMobile.value = mqlMobile?.matches ?? false
    isTablet.value = mqlTablet?.matches ?? false
  }

  onMounted(() => {
    update()
    mqlMobile?.addEventListener?.('change', update)
    mqlTablet?.addEventListener?.('change', update)
  })

  onUnmounted(() => {
    mqlMobile?.removeEventListener?.('change', update)
    mqlTablet?.removeEventListener?.('change', update)
  })

  return { isMobile, isTablet }
}
