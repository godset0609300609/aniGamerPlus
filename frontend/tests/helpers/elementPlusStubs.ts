/**
 * Shared Element Plus component stubs for Vue Test Utils.
 *
 * Production code calls `app.use(ElementPlus)` in `src/main.ts`, which
 * registers every `ElXxx` component globally. In unit tests we stub them
 * so `.text()` assertions and DOM queries are deterministic and don't
 * depend on the real Element Plus render shape (which drags in Popper,
 * transitions, a11y plumbing, etc.).
 *
 * Two helpers are exported:
 *
 * - {@link createElementPlusStubs} — returns an object suitable for
 *   `mount(..., { global: { stubs } })`. Union of every stub the spec
 *   files need; stricter variants win. Per-call overrides merge on top.
 *
 * - {@link elementPlusModuleMock} — the shape to pass to
 *   `vi.mock('element-plus', () => elementPlusModuleMock(...))`, for
 *   specs that import `ElMessage` / `ElDialog` directly from the
 *   package.
 */

import { defineComponent, h, inject, provide, ref, type Component, type Ref } from 'vue'
import { vi } from 'vitest'

// Shared row-provider key so ElTable + ElTableColumn stubs co-operate.
const ROW_KEY = Symbol('stub-row')

// One provider per row — each row scopes its own inject() so slot
// rendering order doesn't matter. Mirrors what the real Element Plus
// table does with renderless row contexts.
const RowProvider = defineComponent({
  props: { row: { type: null, required: true }, index: { type: Number, required: true } },
  setup(props, { slots }) {
    const rowRef = ref(props.row) as Ref<unknown>
    provide(ROW_KEY, rowRef)
    return () =>
      h(
        'tr',
        { class: 'el-table-row', 'data-index': props.index },
        slots.default ? slots.default({ row: props.row, $index: props.index }) : [],
      )
  },
})

// Matches Vue Test Utils' `Stubs = Record<string, Stub>` shape. We type
// stub entries as `Component` — the looser `unknown` caused TS2322 when
// specs handed the return value to `{ global: { stubs } }`.
type StubMap = Record<string, Component>

/**
 * Canonical Element Plus stub map used across component specs.
 *
 * If two spec files previously disagreed on a stub's shape, the more
 * featureful one wins here so both specs keep passing.
 */
export function createElementPlusStubs(overrides: StubMap = {}): StubMap {
  const stubs: StubMap = {
    ElCard: {
      template:
        '<div class="el-card"><div class="header"><slot name="header" /></div><div class="body"><slot /></div></div>',
    },
    ElRow: { template: '<div><slot /></div>' },
    ElCol: { template: '<div><slot /></div>' },
    ElProgress: {
      props: ['percentage'],
      render() {
        return h(
          'div',
          { class: 'el-progress' },
          `${(this as unknown as { percentage: number }).percentage}%`,
        )
      },
    },
    ElAlert: { template: '<div><slot name="title" /><slot /></div>' },
    ElTag: { template: '<span class="el-tag"><slot /></span>' },
    ElButton: {
      props: ['disabled', 'loading', 'type', 'link', 'size'],
      // Vue fallthroughs @click from the parent to the root <button>, so
      // we deliberately do NOT re-emit 'click' here — doing so would
      // double-fire every handler.
      // `data-loading` surfaces the `loading` prop so specs can assert
      // the spinner state without pulling in the real Element Plus
      // button render tree.
      template:
        '<button :disabled="disabled || loading" :data-loading="loading ? \'true\' : \'false\'">' +
        '<slot /></button>',
    },
    ElCollapse: {
      props: ['modelValue'],
      emits: ['update:modelValue'],
      template: '<div class="el-collapse"><slot /></div>',
    },
    ElCollapseItem: {
      props: ['name', 'title'],
      template:
        '<div class="el-collapse-item" :data-group="name">' +
        '<div class="el-collapse-title">{{ title }}</div>' +
        '<div class="el-collapse-body"><slot /></div></div>',
    },
    ElTable: defineComponent({
      props: { data: { type: Array, required: true } },
      setup(props, { slots }) {
        return () =>
          h('table', { class: 'el-table' }, [
            h(
              'tbody',
              props.data.map((row, index) =>
                h(
                  RowProvider,
                  {
                    row,
                    index,
                    key: index,
                    'data-sn': (row as { sn?: number }).sn,
                  },
                  {
                    default: () => (slots.default ? slots.default({ row, $index: index }) : []),
                  },
                ),
              ),
            ),
          ])
      },
    }),
    ElTableColumn: defineComponent({
      props: { label: String, width: [String, Number], minWidth: [String, Number] },
      setup(props, { slots }) {
        const rowRef = inject<Ref<unknown>>(ROW_KEY)
        return () =>
          h(
            'td',
            { class: 'el-table-cell', 'data-label': props.label },
            slots.default ? slots.default({ row: rowRef?.value }) : [],
          )
      },
    }),
    ElSwitch: {
      props: ['modelValue'],
      emits: ['update:modelValue'],
      template:
        '<input type="checkbox" class="el-switch" :checked="modelValue" ' +
        "@change=\"$emit('update:modelValue', ($event.target).checked)\" />",
    },
    ElInput: {
      props: ['modelValue', 'placeholder', 'size', 'type', 'disabled', 'showPassword'],
      emits: ['update:modelValue', 'change', 'blur'],
      template:
        '<input class="el-input" :value="modelValue" :placeholder="placeholder" ' +
        ':disabled="disabled" ' +
        "@input=\"$emit('update:modelValue', ($event.target).value)\" " +
        "@change=\"$emit('change', ($event.target).value)\" " +
        "@blur=\"$emit('blur', $event)\" />",
    },
    ElInputNumber: {
      props: ['modelValue', 'min', 'controls', 'size'],
      emits: ['update:modelValue'],
      template:
        '<input type="number" class="el-input-number" :value="modelValue" ' +
        "@input=\"$emit('update:modelValue', Number(($event.target).value))\" />",
    },
    ElSelect: {
      props: ['modelValue', 'placeholder', 'clearable', 'size'],
      emits: ['update:modelValue'],
      template:
        "<select class=\"el-select\" :value=\"modelValue ?? ''\" " +
        "@change=\"$emit('update:modelValue', ($event.target).value || null)\">" +
        '<option value="">{{ placeholder }}</option><slot /></select>',
    },
    ElOption: {
      props: ['label', 'value'],
      template: '<option :value="value">{{ label }}</option>',
    },
    ElDialog: { template: '<div><slot /><slot name="footer" /></div>' },
    ElForm: { template: '<form><slot /></form>' },
    ElFormItem: { template: '<div><slot /></div>' },
    ElDropdown: {
      template:
        '<div class="el-dropdown"><slot /><slot name="dropdown" /></div>',
    },
    ElDropdownMenu: { template: '<ul class="el-dropdown-menu"><slot /></ul>' },
    ElDropdownItem: {
      props: ['disabled', 'divided'],
      template: '<li class="el-dropdown-item"><slot /></li>',
    },
    ElScrollbar: {
      props: ['maxHeight'],
      template: '<div class="el-scrollbar"><slot /></div>',
    },
    ElPopover: {
      props: ['placement', 'trigger', 'width'],
      template: '<div class="el-popover"><slot name="reference" /><slot /></div>',
    },
    ElBadge: {
      props: ['value', 'max'],
      template: '<div class="el-badge" :data-value="value"><slot /></div>',
    },
    ElSkeleton: {
      props: ['rows', 'animated'],
      template: '<div class="el-skeleton"><slot /></div>',
    },
    ElEmpty: {
      props: ['description'],
      template: '<div class="el-empty">{{ description }}</div>',
    },
    ElContainer: {
      props: ['direction'],
      template: '<div class="el-container"><slot /></div>',
    },
    ElHeader: {
      template: '<header class="el-header"><slot /></header>',
    },
    ElMain: {
      template: '<main class="el-main"><slot /></main>',
    },
    ElMenu: {
      props: ['mode', 'router', 'defaultActive', 'ellipsis'],
      template: '<nav class="el-menu"><slot /></nav>',
    },
    ElMenuItem: {
      props: ['index'],
      template: '<div class="el-menu-item"><slot /></div>',
    },
    ElIcon: {
      props: ['size', 'color'],
      template: '<span class="el-icon"><slot /></span>',
    },
  }
  return { ...stubs, ...overrides }
}

/**
 * Module-level mock payload for `vi.mock('element-plus', ...)`.
 *
 * Specs that call into Element Plus' imperative APIs (`ElMessage.error`,
 * `ElMessageBox.confirm`) want them to be `vi.fn()`s they can assert on.
 * Those specs also typically resolve component registrations at import
 * time, so each ElXxx must exist on the mocked module too.
 *
 * `imperatives` lets callers override the default `vi.fn()`s (for
 * example, to return a resolved promise or track calls with a different
 * mock instance).
 */
export function elementPlusModuleMock(
  imperatives: Record<string, unknown> = {},
): Record<string, unknown> {
  const defaults: Record<string, unknown> = {
    ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
    ElMessageBox: { confirm: vi.fn(), alert: vi.fn(), prompt: vi.fn() },
    ElNotification: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  }
  // Element Plus also needs its component identifiers to resolve at
  // import time — spread the stub map in so `import { ElDialog } from
  // 'element-plus'` works in specs that want to read component props.
  return {
    ...defaults,
    ...createElementPlusStubs(),
    ...imperatives,
  }
}

export { ROW_KEY as __stubRowKey }
