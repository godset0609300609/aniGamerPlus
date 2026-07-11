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

import { computed, defineComponent, Fragment, h, inject, provide, ref, type Component, type Ref, type VNode } from 'vue'
import { vi } from 'vitest'

// v-for directly on a component (e.g. `<el-tab-pane v-for="tab in tabs" ... />`)
// compiles to a single keyed Fragment vnode wrapping the individual panes,
// rather than each pane appearing as its own top-level entry in
// `slots.default()`. Real Element Plus flattens this internally; the ElTabs
// stub below needs the same flattening so dynamically-generated tab-panes
// (not just statically-written ones) resolve to real nav items.
function flattenSlotChildren(vnodes: VNode[]): VNode[] {
  const out: VNode[] = []
  for (const vnode of vnodes) {
    if (vnode.type === Fragment && Array.isArray(vnode.children)) {
      out.push(...flattenSlotChildren(vnode.children as VNode[]))
    } else {
      out.push(vnode)
    }
  }
  return out
}

// Shared row-provider key so ElTable + ElTableColumn stubs co-operate.
const ROW_KEY = Symbol('stub-row')
// Tells ElTableColumn it's being invoked for the header pass (see
// HeaderProvider) rather than a data row, so it renders a <th data-label>
// instead of evaluating the row's #default slot content.
const HEADER_KEY = Symbol('stub-table-header')
// Shared context key so ElRadioButton can read the group's current value
// and tell the group to update it, mirroring real Element Plus's
// inject/provide wiring between ElRadioGroup and its children.
const RADIO_GROUP_KEY = Symbol('stub-radio-group')

// One provider per row — each row scopes its own inject() so slot
// rendering order doesn't matter. Mirrors what the real Element Plus
// table does with renderless row contexts.
const RowProvider = defineComponent({
  props: { row: { type: null, required: true }, index: { type: Number, required: true } },
  setup(props, { slots }) {
    // computed(), not ref(props.row) — a plain ref snapshots props.row once
    // at setup() and never updates when Vue patches this same keyed
    // instance with a new row (e.g. after a parent-side filter shrinks the
    // table's data array). computed() re-derives on every access instead.
    const rowRef = computed(() => props.row) as Ref<unknown>
    provide(ROW_KEY, rowRef)
    return () =>
      h(
        'tr',
        { class: 'el-table-row', 'data-index': props.index },
        slots.default ? slots.default({ row: props.row, $index: props.index }) : [],
      )
  },
})

// Renders the column labels once as a real header row. Real Element Plus
// tables render a <th> per column with its `label`; specs that assert on
// visible column headers (e.g. an optional column's label appearing/
// disappearing) need that same shape rather than just cell values.
const HeaderProvider = defineComponent({
  setup(_props, { slots }) {
    provide(ROW_KEY, computed(() => undefined))
    provide(HEADER_KEY, true)
    return () =>
      h(
        'tr',
        { class: 'el-table-header-row' },
        slots.default ? slots.default({ row: undefined, $index: -1 }) : [],
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
    ElTag: {
      // `closable` (and friends) declared with `type: Boolean` so Vue
      // applies its empty-attribute-means-true coercion for the bare
      // `<el-tag closable>` shorthand used across the app — array-style
      // prop declarations carry no type info and would leave `closable`
      // as the falsy string `''`.
      props: {
        type: String,
        effect: String,
        round: Boolean,
        closable: Boolean,
        size: String,
      },
      emits: ['close'],
      template:
        '<span class="el-tag" :data-type="type">' +
        '<slot />' +
        '<button v-if="closable" class="el-tag__close" @click="$emit(\'close\')">×</button>' +
        '</span>',
    },
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
            h('thead', {}, [h(HeaderProvider, {}, { default: slots.default })]),
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
        const isHeader = inject<boolean>(HEADER_KEY, false)
        return () =>
          isHeader
            ? h('th', { class: 'el-table-header-cell', 'data-label': props.label }, props.label)
            : h(
                'td',
                { class: 'el-table-cell', 'data-label': props.label },
                slots.default ? slots.default({ row: rowRef?.value }) : [],
              )
      },
    }),
    ElSwitch: {
      props: ['modelValue', 'disabled'],
      emits: ['update:modelValue', 'change'],
      template:
        '<input type="checkbox" class="el-switch" :checked="modelValue" :disabled="disabled" ' +
        "@change=\"$emit('update:modelValue', ($event.target).checked); $emit('change', ($event.target).checked)\" />",
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
      props: ['modelValue', 'min', 'max', 'controls', 'size', 'disabled'],
      emits: ['update:modelValue'],
      template:
        '<input type="number" class="el-input-number" :value="modelValue" :disabled="disabled" ' +
        "@input=\"$emit('update:modelValue', Number(($event.target).value))\" />",
    },
    // Mirrors the real component's `fetch-suggestions(query, cb)` contract:
    // every keystroke invokes the prop, the caller's `cb(results)` populates
    // the rendered suggestion list, and clicking one emits `select` (plus
    // clears the list, matching real Autocomplete's post-select behavior).
    ElAutocomplete: defineComponent({
      props: {
        modelValue: { type: [String, Number], default: '' },
        placeholder: String,
        clearable: Boolean,
        fetchSuggestions: { type: Function, default: null },
      },
      emits: ['update:modelValue', 'input', 'select', 'clear'],
      setup(props, { emit, slots }) {
        const suggestions = ref<Record<string, unknown>[]>([])
        function onInput(event: Event): void {
          const value = (event.target as HTMLInputElement).value
          emit('update:modelValue', value)
          emit('input', value)
          const fetchSuggestions = props.fetchSuggestions as
            | ((q: string, cb: (data: Record<string, unknown>[]) => void) => void)
            | null
          if (fetchSuggestions) {
            fetchSuggestions(value, (data) => {
              suggestions.value = data ?? []
            })
          }
        }
        function onSelect(item: Record<string, unknown>): void {
          emit('select', item)
          suggestions.value = []
        }
        return () =>
          h('div', { class: 'el-autocomplete-stub' }, [
            h('input', {
              class: 'el-autocomplete',
              value: props.modelValue,
              placeholder: props.placeholder,
              onInput,
            }),
            h(
              'div',
              { class: 'el-autocomplete-suggestions' },
              suggestions.value.map((item, index) =>
                h(
                  'div',
                  {
                    class: 'el-autocomplete-suggestion-item',
                    key: index,
                    onClick: () => onSelect(item),
                  },
                  slots.default ? slots.default({ item }) : [],
                ),
              ),
            ),
          ])
      },
    }),
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
    // NOTE: content renders unconditionally (not gated behind `v-model`)
    // to match the pre-existing stub's behaviour — many specs query dialog
    // content without first flushing the open transition. `fullscreen` is
    // still exposed as a real prop (surfaced via `data-fullscreen`) so
    // specs can assert on it directly.
    ElDialog: {
      props: ['modelValue', 'title', 'width', 'fullscreen', 'closeOnClickModal'],
      emits: ['update:modelValue', 'close'],
      template:
        '<div class="el-dialog" :data-fullscreen="fullscreen ? \'true\' : \'false\'">' +
        '<slot /><slot name="footer" /></div>',
    },
    ElDrawer: {
      props: ['modelValue', 'direction', 'size', 'withHeader'],
      emits: ['update:modelValue'],
      template:
        '<div v-if="modelValue" class="el-drawer" :data-direction="direction"><slot /></div>',
    },
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
    ElTooltip: {
      props: ['content', 'placement', 'effect', 'disabled', 'showAfter'],
      template: '<div class="el-tooltip" :title="content" :data-content="content"><slot /></div>',
    },
    ElTabs: defineComponent({
      props: { modelValue: { type: String, default: '' } },
      emits: ['update:modelValue'],
      setup(props, { slots, emit }) {
        return () => {
          const panes = flattenSlotChildren(slots.default ? slots.default() : [])
          return h('div', { class: 'el-tabs' }, [
            h(
              'div',
              { class: 'el-tabs__nav' },
              panes.map((vnode) => {
                const vProps = vnode.props as { name?: string; label?: string } | null
                return h(
                  'div',
                  {
                    class: [
                      'el-tabs__item',
                      { 'is-active': vProps?.name === props.modelValue },
                    ],
                    'data-name': vProps?.name,
                    onClick: () => emit('update:modelValue', vProps?.name),
                  },
                  vProps?.label,
                )
              }),
            ),
            h('div', { class: 'el-tabs__content' }, panes),
          ])
        }
      },
    }),
    ElTabPane: {
      props: ['label', 'name'],
      template: '<div class="el-tab-pane" :data-name="name"><slot /></div>',
    },
    ElSteps: {
      props: ['active', 'finishStatus', 'simple'],
      template: '<div class="el-steps" :data-active="active"><slot /></div>',
    },
    ElStep: {
      props: ['title'],
      template: '<div class="el-step">{{ title }}</div>',
    },
    ElCheckboxGroup: {
      props: ['modelValue'],
      emits: ['update:modelValue'],
      template: '<div class="el-checkbox-group"><slot /></div>',
    },
    ElPagination: defineComponent({
      props: {
        total: { type: Number, default: 0 },
        currentPage: { type: Number, default: 1 },
        pageSize: { type: Number, default: 10 },
        pageSizes: { type: Array, default: () => [10, 20, 50, 100] },
        layout: { type: String, default: '' },
      },
      emits: ['update:currentPage', 'update:pageSize', 'size-change', 'current-change'],
      setup(props, { emit }) {
        function goToPage(p: number): void {
          if (p < 1) return
          emit('update:currentPage', p)
          emit('current-change', p)
        }
        function changeSize(event: Event): void {
          const size = Number((event.target as HTMLSelectElement).value)
          emit('update:pageSize', size)
          emit('size-change', size)
        }
        return () =>
          h('div', { class: 'el-pagination' }, [
            h('span', { class: 'el-pagination-total' }, `共 ${props.total} 筆`),
            h(
              'select',
              {
                class: 'el-pagination-sizes',
                value: props.pageSize,
                onChange: changeSize,
              },
              (props.pageSizes as number[]).map((s) => h('option', { value: s }, `${s}/頁`)),
            ),
            h(
              'button',
              {
                class: 'el-pagination-prev',
                disabled: props.currentPage <= 1,
                onClick: () => goToPage(props.currentPage - 1),
              },
              '上一頁',
            ),
            h('span', { class: 'el-pagination-pager' }, String(props.currentPage)),
            h(
              'button',
              {
                class: 'el-pagination-next',
                onClick: () => goToPage(props.currentPage + 1),
              },
              '下一頁',
            ),
          ])
      },
    }),
    ElCheckbox: {
      props: ['modelValue', 'label', 'disabled'],
      emits: ['update:modelValue', 'change'],
      template:
        '<label class="el-checkbox">' +
        '<input type="checkbox" :checked="modelValue" :disabled="disabled" ' +
        "@change=\"$emit('update:modelValue', $event.target.checked)\" />" +
        '<slot>{{ label }}</slot></label>',
    },
    ElRadioGroup: defineComponent({
      props: { modelValue: { type: [String, Number, Boolean], default: null }, size: String },
      emits: ['update:modelValue'],
      setup(props, { emit, slots }) {
        provide(RADIO_GROUP_KEY, {
          modelValue: computed(() => props.modelValue),
          select: (value: unknown) => emit('update:modelValue', value),
        })
        return () => h('div', { class: 'el-radio-group' }, slots.default ? slots.default() : [])
      },
    }),
    ElRadioButton: defineComponent({
      props: { label: { type: [String, Number, Boolean], default: null } },
      setup(props, { slots }) {
        const group = inject<
          { modelValue: Ref<unknown>; select: (value: unknown) => void } | null
        >(RADIO_GROUP_KEY, null)
        return () =>
          h(
            'label',
            {
              class: [
                'el-radio-button',
                { 'is-active': group?.modelValue.value === props.label },
              ],
              'data-label': props.label,
              onClick: () => group?.select(props.label),
            },
            slots.default ? slots.default() : [],
          )
      },
    }),
    ElAvatar: {
      props: ['size', 'src', 'shape'],
      template:
        '<div class="el-avatar" :style="{ width: (size || 20) + \'px\', height: (size || 20) + \'px\' }"><slot /></div>',
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
