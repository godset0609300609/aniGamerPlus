/**
 * Playwright globalSetup — run once before all specs.
 *
 * 1. Creates an isolated temp workspace directory.
 * 2. Writes a minimal config.json with auth.enabled=false so the SPA
 *    bypasses Discord OAuth and treats every visitor as an admin.
 * 3. Runs alembic migrations to initialise an empty aniGamer.db.
 * 4. Spawns the scheduler (port 15001) and API (port 15000) processes.
 * 5. Waits for /health on the API to return status ok.
 * 6. Saves PIDs to a file so globalTeardown can kill them.
 */
import { execSync } from 'node:child_process'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import {
  backendDir,
  E2E_API_PORT,
  spawnApi,
  spawnScheduler,
  waitForPort,
} from './fixtures/server'

const PID_FILE = path.join(os.tmpdir(), 'anigamerplus-e2e-pids.json')

/** Minimal config.json for E2E tests.
 *
 * Key settings:
 *  - auth.enabled = false  → frontend/backend skip Discord OAuth
 *  - check_frequency = 1440  → scheduler won't auto-scan during tests
 *  - download_cd = 0, parse_cd = 0  → no cooldowns
 *  - dashboard.port = 15000  → API listens on the test port
 */
function buildTestConfig(workspaceDir: string): object {
  return {
    bangumi_dir: path.join(workspaceDir, 'bangumi'),
    temp_dir: path.join(workspaceDir, 'temp'),
    classify_bangumi: true,
    check_frequency: 1440,
    download_cd: 0,
    parse_sn_cd: 0,
    parse_cd: 0,
    download_resolution: '1080',
    lock_resolution: false,
    only_use_vip: false,
    default_download_mode: 'latest',
    'multi-thread': 1,
    multi_upload: 1,
    segment_download_mode: false,
    multi_downloading_segment: 1,
    segment_max_retry: 3,
    add_bangumi_name_to_video_filename: true,
    add_resolution_to_video_filename: true,
    customized_video_filename_prefix: '',
    customized_bangumi_name_suffix: '',
    customized_video_filename_suffix: '',
    video_filename_extension: 'mp4',
    zerofill: 1,
    ua: 'Mozilla/5.0 (E2E-Test)',
    use_proxy: false,
    proxy: '',
    upload_to_server: false,
    user_command: '',
    coolq_notify: false,
    faststart_movflags: false,
    audio_language: false,
    use_mobile_api: false,
    danmu: false,
    read_sn_list_when_checking_update: false,
    read_config_when_checking_update: false,
    ads_time: 0,
    mobile_ads_time: 0,
    use_dashboard: true,
    dashboard: {
      host: '127.0.0.1',
      port: E2E_API_PORT,
      SSL: false,
    },
    save_logs: true,
    quantity_of_logs: 3,
    config_version: 13.0,
    database_version: 2.0,
    auth: {
      enabled: false,
      session_secret: 'e2e-test-session-secret',
      client_id: '',
      client_secret: '',
      redirect_uri: 'http://localhost:15000/api/auth/callback',
      bootstrap_admin_ids: [],
    },
  }
}

export default async function globalSetup(): Promise<void> {
  // --- 1. Create isolated temp workspace ---------------------------------
  const workspaceDir = fs.mkdtempSync(path.join(os.tmpdir(), 'anigamerplus-e2e-'))
  console.log(`[e2e globalSetup] workspace: ${workspaceDir}`)

  // Create subdirectories the backend expects.
  for (const sub of ['bangumi', 'temp', 'logs']) {
    fs.mkdirSync(path.join(workspaceDir, sub), { recursive: true })
  }

  // --- 2. Write config.json -----------------------------------------------
  const configPath = path.join(workspaceDir, 'config.json')
  fs.writeFileSync(configPath, JSON.stringify(buildTestConfig(workspaceDir), null, 2), 'utf-8')

  // --- 3. Run alembic migrations ------------------------------------------
  const dbPath = path.join(workspaceDir, 'aniGamer.db')
  try {
    execSync('uv run --project . alembic upgrade head', {
      cwd: backendDir(),
      env: {
        ...process.env,
        ANIGAMERPLUS_WORKSPACE_DIR: workspaceDir,
        ANIGAMERPLUS_DB_PATH: dbPath,
      },
      stdio: 'pipe',
    })
    console.log('[e2e globalSetup] alembic migrations OK')
  } catch (err) {
    console.error('[e2e globalSetup] alembic failed:', err)
    throw err
  }

  // --- 4. Spawn backend processes -----------------------------------------
  const logDir = path.join(workspaceDir, 'process-logs')
  fs.mkdirSync(logDir, { recursive: true })

  const schedulerProc = spawnScheduler(workspaceDir, path.join(logDir, 'scheduler.log'))
  const apiProc = spawnApi(workspaceDir, path.join(logDir, 'api.log'))

  console.log(`[e2e globalSetup] scheduler PID=${schedulerProc.pid} API PID=${apiProc.pid}`)

  // --- 5. Wait for API health ---------------------------------------------
  const apiHealthUrl = `http://127.0.0.1:${E2E_API_PORT}/api/health`
  console.log(`[e2e globalSetup] waiting for ${apiHealthUrl}…`)
  const up = await waitForPort(apiHealthUrl, 30_000)
  if (!up) {
    const apiLog = path.join(logDir, 'api.log')
    let tail = ''
    try {
      tail = fs.readFileSync(apiLog, 'utf-8').slice(-2000)
    } catch {
      tail = '(log unavailable)'
    }
    throw new Error(
      `[e2e globalSetup] API did not become ready in 30 s.\nLast API log:\n${tail}`,
    )
  }
  console.log('[e2e globalSetup] API is up')

  // --- 6. Save PIDs + workspaceDir for teardown ---------------------------
  fs.writeFileSync(
    PID_FILE,
    JSON.stringify({
      workspaceDir,
      apiPid: apiProc.pid,
      schedulerPid: schedulerProc.pid,
    }),
    'utf-8',
  )
}
