# aniGamerPlus frontend (Vue 3 + Vite)

Vite + Vue 3 + TypeScript + Element Plus SPA for the aniGamerPlus dashboard.

## Setup

```bash
cd frontend
npm install
```

## Develop

```bash
npm run dev
```

This starts Vite on http://localhost:5173 and proxies `/api` (including the
WebSocket on `/api/ws/tasks_progress`) to the FastAPI backend. The backend URL
defaults to `http://127.0.0.1:5000`; override with `VITE_BACKEND_URL=...`.

Start the backend separately in another terminal:

```bash
cd ../backend
uv run anigamerplus-server
```

## Build

```bash
npm run build
```

Output is written to `dist/`. Serve it behind any static host — the built
`index.html` loads all assets relative to its own location, so you can mount it
at any path.

## Test

```bash
npm run test          # vitest, single run
npm run test:watch    # vitest, watch mode
npm run typecheck     # vue-tsc --noEmit
```

Tests live in `tests/` and cover:

- `tests/api/client.spec.ts` — the generic HTTP client
- `tests/api/config.spec.ts` — config API + proxy-string (de)serialisation
- `tests/api/tasks.spec.ts` — sn extraction + manual task submit
- `tests/api/ws.spec.ts` — the WebSocket progress stream
- `tests/components/ManualTaskDialog.spec.ts`
- `tests/components/MonitorView.spec.ts`
- `tests/components/AnimeListView.spec.ts`

Element Plus is registered globally in production (`src/main.ts`), so in
component specs we stub each `ElXxx` at mount time. The canonical stub set
lives in **`tests/helpers/elementPlusStubs.ts`** — prefer importing
`createElementPlusStubs()` (for `global.stubs`) or `elementPlusModuleMock()`
(for `vi.mock('element-plus', ...)`) from there rather than copy-pasting
component stubs per spec. If a new spec needs a stricter stub for some
component, extend the helper rather than re-declaring inline.

End-to-end (Playwright) is intentionally out-of-scope for now — stand up a
`frontend/e2e/` directory if / when you want it.
