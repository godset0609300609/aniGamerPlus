export default {
  packageManager: 'npm',
  testRunner: 'vitest',
  // TypeScript checker is disabled because existing test files contain TS errors
  // (SnListDialog.spec.ts uses modelValue which fails strict tsc but passes at runtime).
  // Re-enable once test files are type-clean: checkers: ['typescript'], tsconfigFile: 'tsconfig.json'
  mutate: [
    'src/**/*.{ts,vue}',
    '!src/**/*.spec.ts',
    '!src/main.ts',
    '!src/types.ts',
    '!src/env.d.ts',
  ],
  thresholds: { high: 95, low: 80, break: null },
  coverageAnalysis: 'perTest',
  reporters: ['html', 'clear-text', 'progress'],
  timeoutMS: 30000,
};
