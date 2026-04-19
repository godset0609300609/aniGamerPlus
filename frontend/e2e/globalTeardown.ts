/**
 * Playwright globalTeardown — run once after all specs.
 *
 * Reads the PID file written by globalSetup, kills both backend processes,
 * then removes the temp workspace directory.
 *
 * On Windows, `process.kill(pid, 'SIGTERM')` is unreliable for child
 * processes spawned by `spawn()`.  We use `taskkill /F /PID` instead to
 * ensure the process tree is forcibly terminated.
 */
import { execSync } from 'node:child_process'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

const PID_FILE = path.join(os.tmpdir(), 'anigamerplus-e2e-pids.json')
const IS_WINDOWS = process.platform === 'win32'

function killPid(pid: number): void {
  try {
    if (IS_WINDOWS) {
      // /F = force, /T = kill process tree (children too)
      execSync(`taskkill /F /T /PID ${pid}`, { stdio: 'pipe' })
    } else {
      process.kill(pid, 'SIGTERM')
      // Give it a moment then SIGKILL if still running.
      setTimeout(() => {
        try {
          process.kill(pid, 'SIGKILL')
        } catch {
          // Already gone — expected.
        }
      }, 3_000)
    }
  } catch {
    // Process may have already exited.
  }
}

/** Kill any process currently listening on the given port (Windows only). */
function killPort(port: number): void {
  if (!IS_WINDOWS) return
  try {
    // netstat output format: TCP  127.0.0.1:<port>  0.0.0.0:0  LISTENING  <pid>
    const out = execSync(
      `netstat -ano | findstr "LISTENING" | findstr ":${port}"`,
      { encoding: 'utf-8', stdio: 'pipe' },
    )
    const match = out.match(/\s+(\d+)\s*$/)
    if (match) {
      const pid = parseInt(match[1], 10)
      console.log(`[e2e globalTeardown] killing port ${port} PID=${pid}`)
      try {
        execSync(`taskkill /F /T /PID ${pid}`, { stdio: 'pipe' })
      } catch {
        // Already gone.
      }
    }
  } catch {
    // Port not in use — nothing to kill.
  }
}

export default async function globalTeardown(): Promise<void> {
  let workspaceDir: string | null = null

  if (fs.existsSync(PID_FILE)) {
    try {
      const data = JSON.parse(fs.readFileSync(PID_FILE, 'utf-8')) as {
        workspaceDir: string
        apiPid: number
        schedulerPid: number
      }
      workspaceDir = data.workspaceDir

      console.log(`[e2e globalTeardown] killing API PID=${data.apiPid}`)
      killPid(data.apiPid)

      console.log(`[e2e globalTeardown] killing scheduler PID=${data.schedulerPid}`)
      killPid(data.schedulerPid)

      // On Windows, also kill any stray processes on the E2E ports in case
      // uvicorn was reparented and escaped the process-tree kill.
      if (IS_WINDOWS) {
        killPort(15000)
        killPort(15001)
      }

      // Wait briefly for graceful exit before removing workspace.
      // On Windows, taskkill /F is synchronous so no wait is needed.
      if (!IS_WINDOWS) {
        await new Promise((r) => setTimeout(r, 4_000))
      }
    } catch (err) {
      console.error('[e2e globalTeardown] error reading PID file:', err)
    } finally {
      fs.rmSync(PID_FILE, { force: true })
    }
  }

  if (workspaceDir && fs.existsSync(workspaceDir)) {
    console.log(`[e2e globalTeardown] removing workspace: ${workspaceDir}`)
    try {
      fs.rmSync(workspaceDir, { recursive: true, force: true })
    } catch (err) {
      console.error('[e2e globalTeardown] failed to remove workspace:', err)
    }
  }
}
