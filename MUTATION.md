# Mutation Testing

## What is mutation testing?

Mutation testing is a technique for measuring the effectiveness of a test suite. A tool automatically
introduces small code changes ("mutants") — such as flipping `>` to `>=`, replacing `&&` with `||`, or
deleting a function argument — then re-runs the tests. If at least one test fails for a given mutant,
the mutant is "killed" and the tests are proving their worth. If all tests still pass for a mutant, the
mutant "survives", indicating a gap in the test suite that regular coverage metrics would miss. The
mutation score (killed / total) is a stronger quality signal than line coverage alone.

---

## Backend — mutmut

### Tool

[mutmut](https://github.com/boxed/mutmut) 3.x (installed as a dev dependency via `uv`).

> **Note:** mutmut 3.x requires Linux/WSL on Windows. Run via WSL or in CI (Linux).

### Run (demo module only)

```bash
cd backend
# First run: generates mutants + runs stats
uv run mutmut run 'app.scheduler.cd_counter.*'

# View results
uv run mutmut results
```

### Run (full repo — nightly)

```bash
cd backend
# WARNING: 8,782 mutants across all app/ files; this takes 30–90 minutes.
uv run mutmut run
uv run mutmut results
```

### Configuration

`backend/pyproject.toml` `[tool.mutmut]` section:
- `paths_to_mutate = ["app/"]` — mutates all app source files when running full-repo.
- `pytest_add_cli_args_test_selection = ["tests/test_cd_counter.py"]` — restricts stats collection
  to the demo test file for the scoped run; remove this line for full-repo nightly runs.
- `pytest_add_cli_args = ["--no-cov", "--override-ini=addopts="]` — disables coverage collection
  during mutation testing (incompatible with mutmut's multi-process runner).

### Demo module results

| Module | Total mutants | Killed | Survived | Kill rate |
|--------|--------------|--------|----------|-----------|
| `app/scheduler/cd_counter.py` | 57 | 56 | 1 | **98.2%** |

**Surviving mutant — `xǁDownloadCooldownǁ__init____mutmut_1`:**

```diff
-def __init__(self, seconds: int, logger: Logger, *, label: str = '下載冷卻') -> None:
+def __init__(self, seconds: int, logger: Logger, *, label: str = 'XX下載冷卻XX') -> None:
```

This mutant changes the default value of the `label` keyword argument. In mutmut 3.x the trampoline
mechanism wraps `__init__` so that calls are always dispatched through the original default-parameter
signature before selecting the mutant body. Because the mutant default is never actually used (callers
see the original default from the trampoline), the test `test_default_label_is_exact_string` cannot
distinguish the mutant. This is a **structural equivalent mutant** caused by the trampoline design;
it does not represent a real test gap.

---

## Frontend — Stryker

### Tool

[Stryker Mutator](https://stryker-mutator.io/) with the `@stryker-mutator/vitest-runner` plugin.

> **Note on TypeScript checker:** `@stryker-mutator/typescript-checker` is installed but disabled in
> `stryker.config.mjs`. `tests/components/SnListDialog.spec.ts` contains `modelValue` props that pass
> vue-tsc (which uses component-aware type checking) but fail standalone tsc (which Stryker's checker
> uses). Re-enable by adding `checkers: ['typescript'], tsconfigFile: 'tsconfig.json'` after fixing
> the SnListDialog test prop types.

### Run (demo module only)

```bash
cd frontend
npx stryker run --mutate "src/composables/useTaskCategory.ts"
```

### Run (full repo — nightly)

```bash
cd frontend
# WARNING: 156 source files; this takes 10–30 minutes.
npm run mutation
```

### Configuration

`frontend/stryker.config.mjs`:
- `coverageAnalysis: 'perTest'` — only runs tests that cover each mutant (fast).
- `thresholds: { high: 95, low: 80, break: null }` — reports but does not fail below threshold.
- `timeoutMS: 30000` — kills mutant runs that take longer than 30 s.

### Demo module results

| Module | Total mutants | Killed | Survived | Kill rate |
|--------|--------------|--------|----------|-----------|
| `src/composables/useTaskCategory.ts` | 42 | 41 | 1 | **97.6%** |

**Surviving mutant:**

```diff
-    if (Number.isNaN(t)) return false;
+    if (false) return false;
```

This mutant removes the NaN guard. It is an **equivalent mutant**: `Date.now() - NaN = NaN`, and
`NaN <= any_number` is always `false` in JavaScript, so the function returns `false` with or without
the guard. No test can distinguish the two without changing the observable return value.

---

## Nightly CI

See `.github/workflows/mutation.yml` for the CI scaffold (currently gated with `if: false`).
Enable by removing or changing the gate condition, and un-comment the threshold assertions.

---

## Notes

- Do **not** run full-repo mutation testing in PR CI — it is too slow (30–90 min backend, 10–30 min frontend).
- A kill rate of >= 95% is the target for any module added to the demo scope.
- To add a new module: add its test file to `pytest_add_cli_args_test_selection` (backend) or use
  `npx stryker run --mutate "path/to/file.ts"` (frontend) and iterate until >= 95% killed.
