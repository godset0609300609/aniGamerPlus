/**
 * Helpers to spawn the aniGamerPlus backend processes for E2E tests.
 *
 * Two processes are started:
 *   - API:       uv run anigamerplus-api  (port 15000 via env)
 *   - Scheduler: uv run anigamerplus-scheduler (port 15001 via env)
 *
 * Both are given:
 *   ANIGAMERPLUS_WORKSPACE_DIR  — isolated temp workspace created by globalSetup
 *   ANIGAMERPLUS_INTERNAL_SECRET — shared secret so API can reach scheduler
 *
 * The API process also gets ANIGAMERPLUS_DISABLE_SCHEDULER=1 so it does not
 * spin up a second in-process UpdateLoop (the scheduler process does that).
 */
import { type ChildProcess, spawn } from 'node:child_process'
import * as fs from 'node:fs'
import * as path from 'node:path'
import { fileURLToPath } from 'node:url'

export const E2E_API_PORT = 15000
export const E2E_SCHEDULER_PORT = 15001
export const E2E_INTERNAL_SECRET = 'e2e-test-secret'

/** Absolute path to the backend package directory. */
export function backendDir(): string {
  // frontend/e2e/fixtures/server.ts  →  frontend/../backend
  const currentFile = fileURLToPath(import.meta.url)
  return path.resolve(path.dirname(currentFile), '..', '..', '..', 'backend')
}

/** Base env vars shared by both processes. */
function baseEnv(workspaceDir: string): NodeJS.ProcessEnv {
  return {
    ...process.env,
    ANIGAMERPLUS_WORKSPACE_DIR: workspaceDir,
    ANIGAMERPLUS_INTERNAL_SECRET: E2E_INTERNAL_SECRET,
  }
}

export interface BackendProcesses {
  api: ChildProcess
  scheduler: ChildProcess
}

/**
 * Spawn the scheduler process.
 * Ports are controlled by the env var ANIGAMERPLUS_SCHEDULER_PORT (custom
 * patch expected) — if not supported the default 5001 is used but the tests
 * still work because the proxy URL can be overridden.
 */
export function spawnScheduler(workspaceDir: string, logFile: string): ChildProcess {
  const out = fs.openSync(logFile, 'a')
  const proc = spawn(
    'uv',
    ['run', '--project', '.', 'anigamerplus-scheduler'],
    {
      cwd: backendDir(),
      env: {
        ...baseEnv(workspaceDir),
        ANIGAMERPLUS_SCHEDULER_PORT: String(E2E_SCHEDULER_PORT),
      },
      stdio: ['ignore', out, out],
      detached: false,
    },
  )
  return proc
}

/**
 * Spawn the API process.
 * `ANIGAMERPLUS_DISABLE_SCHEDULER=1` prevents the API from launching its own
 * UpdateLoop.  Port is read from the config.json dashboard.port field written
 * by globalSetup.
 */
export function spawnApi(workspaceDir: string, logFile: string): ChildProcess {
  const out = fs.openSync(logFile, 'a')
  const proc = spawn(
    'uv',
    ['run', '--project', '.', 'anigamerplus-api'],
    {
      cwd: backendDir(),
      env: {
        ...baseEnv(workspaceDir),
        ANIGAMERPLUS_DISABLE_SCHEDULER: '1',
        ANIGAMERPLUS_SCHEDULER_URL: `http://127.0.0.1:${E2E_SCHEDULER_PORT}`,
        ANIGAMERPLUS_CORS_ORIGINS: 'http://localhost:4173,http://127.0.0.1:4173',
      },
      stdio: ['ignore', out, out],
      detached: false,
    },
  )
  return proc
}

/**
 * Poll a URL until it returns a 200 response (or the timeout elapses).
 * Returns true on success, false on timeout.
 */
export async function waitForPort(
  url: string,
  timeoutMs = 30_000,
  intervalMs = 500,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const resp = await fetch(url, { signal: AbortSignal.timeout(1000) })
      if (resp.ok) return true
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  return false
}

/** Kill a child process and wait for it to exit. */
export function killProcess(proc: ChildProcess): Promise<void> {
  return new Promise((resolve) => {
    if (proc.exitCode !== null) {
      resolve()
      return
    }
    proc.on('exit', () => resolve())
    proc.kill('SIGTERM')
    // Force-kill after 5 s if still running.
    setTimeout(() => {
      try {
        proc.kill('SIGKILL')
      } catch {
        // already gone
      }
    }, 5_000)
  })
}
