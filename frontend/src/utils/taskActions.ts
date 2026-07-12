/**
 * Shared task actions for the monitor UI — used by both TaskCard.vue
 * (kanban) and MonitorTable.vue (table) so the confirm-dialog + error
 * handling around cancelling a task lives in exactly one place.
 */

import { ElMessage, ElMessageBox } from 'element-plus'
import { cancelTask } from '@/api/client'
import { TasksApi } from '@/api/tasks'

const tasksApi = new TasksApi()

/**
 * Confirms with the user, then cancels the given task via the backend.
 * Swallows the dialog-dismissed case silently; surfaces API failures via
 * ElMessage.error.
 */
export async function confirmCancelTask(sn: number): Promise<void> {
  try {
    await ElMessageBox.confirm(`確定要取消任務 sn=${sn} 嗎？`, '取消任務', {
      confirmButtonText: '確定取消',
      cancelButtonText: '保留',
      type: 'warning',
    })
  } catch {
    // User dismissed the dialog — no action needed.
    return
  }

  try {
    await cancelTask(sn)
  } catch (err) {
    ElMessage.error(`取消失敗: ${err instanceof Error ? err.message : String(err)}`)
  }
}

/**
 * Immediately dismisses a task card via the force-finish API — no confirm
 * dialog. This is a "clear this stuck/ghost card off my screen" action, not
 * a destructive one: unlike `confirmCancelTask`, it never signals a live
 * worker to stop, it just marks the live-progress entry terminal so the
 * next WS snapshot excludes it. That is exactly what makes it work for
 * ghost cards a real cancel can't reach (see backend
 * `ProgressService.force_finish`'s docstring), so skipping the confirm
 * dialog keeps this a quick housekeeping action rather than a two-step one.
 */
export async function dismissTask(sn: number): Promise<void> {
  try {
    await tasksApi.dismissTask(sn)
    ElMessage.success('任務已移除')
  } catch (err) {
    ElMessage.error(`移除失敗: ${err instanceof Error ? err.message : String(err)}`)
  }
}
