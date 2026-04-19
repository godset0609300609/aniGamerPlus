<script setup lang="ts">
import { useAuthStore } from '../stores/auth'

const { user, logout } = useAuthStore()
</script>

<template>
  <el-dropdown
    v-if="user"
    trigger="click"
  >
    <span class="user-trigger">
      <img
        v-if="user.avatar_url"
        :src="user.avatar_url"
        :alt="user.username"
        class="user-avatar"
      />
      <span
        v-else
        class="user-avatar user-avatar--placeholder"
      >
        {{ user.username[0]?.toUpperCase() }}
      </span>
      <span class="user-name">{{ user.username }}</span>
    </span>

    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item
          class="user-role-label"
          disabled
        >
          {{ user.role === 'admin' ? '管理員' : '下載者' }}
        </el-dropdown-item>
        <el-dropdown-item
          divided
          @click="logout"
        >
          登出
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<style scoped>
.user-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: #e9ecef;
  outline: none;
}

.user-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  object-fit: cover;
}

.user-avatar--placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: #4caf50;
  color: white;
  font-weight: 700;
  font-size: 14px;
}

.user-name {
  font-size: 14px;
}

.user-role-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
