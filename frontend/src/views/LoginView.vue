<script setup lang="ts">
function loginWithDiscord(): void {
  window.location.href = '/api/auth/login'
}
</script>

<template>
  <div class="login-shell">
    <el-card class="login-card">
      <template #header>
        <div class="login-title">
          動畫管家
        </div>
      </template>
      <p class="login-desc">
        請以 Discord 帳號登入以繼續使用。
      </p>
      <el-button
        type="primary"
        size="large"
        class="login-btn"
        @click="loginWithDiscord"
      >
        以 Discord 登入
      </el-button>
    </el-card>
  </div>
</template>

<style scoped>
.login-shell {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background-color: var(--el-bg-color-page, #f5f5f5);
}

.login-card {
  width: 360px;
  text-align: center;
}

.login-title {
  font-size: 20px;
  font-weight: 700;
}

.login-desc {
  margin-bottom: 24px;
  color: var(--el-text-color-secondary, #666);
}

.login-btn {
  width: 100%;
}
</style>
