<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { SnListApi } from '@/api/snlist'
import { useBreakpoint } from '@/composables/useBreakpoint'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

const api = new SnListApi()
const { isMobile } = useBreakpoint()
const content = ref('')
const saving = ref(false)

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

watch(visible, async (open) => {
  if (!open) return
  try {
    content.value = await api.load()
  } catch (err) {
    ElMessage.error(`讀取 sn_list 失敗: ${(err as Error).message}`)
  }
})

async function submit(): Promise<void> {
  saving.value = true
  try {
    await api.save(content.value)
    ElMessage.success('sn_list 已更新')
    visible.value = false
  } catch (err) {
    ElMessage.error(`sn_list 更新失敗: ${(err as Error).message}`)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="sn_list"
    :width="isMobile ? '100%' : '720px'"
    :fullscreen="isMobile"
  >
    <div class="ag-snlist-help">
      <p><strong>格式：</strong></p>
      <pre>@分類(可空)
sn碼 下載模式(可空)  &lt;重命名&gt;(可空)  #注釋(可空)</pre>
      <ul>
        <li>注釋 <code>#</code> 後面的所有字符程序均不會讀取。</li>
        <li>
          <code>@</code> 開頭為番劇分類名, 番劇會歸類在此分類名的資料夾下; 單獨
          <code>@</code> 表示不分類。
        </li>
        <li>用 <code>&lt;</code> 與 <code>&gt;</code> 框起來的名字會當作番劇目錄名。</li>
      </ul>
    </div>
    <el-input
      v-model="content"
      type="textarea"
      :rows="10"
      placeholder="貼上你的 sn_list 內容"
    />
    <template #footer>
      <el-button @click="visible = false">
        關閉
      </el-button>
      <el-button
        type="success"
        :loading="saving"
        @click="submit"
      >
        提交
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.ag-snlist-help {
  margin-bottom: 12px;
  background: #f5f7fa;
  padding: 12px 16px;
  border-radius: 8px;
  font-size: 13px;
}
.ag-snlist-help pre {
  margin: 8px 0;
  background: white;
  padding: 8px;
  border-radius: 4px;
}
</style>
