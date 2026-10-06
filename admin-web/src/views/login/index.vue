<template>
  <div class="login-page">
    <el-card class="login-card">
      <div class="login-title">工程现场智能巡查助手</div>
      <div class="login-sub">管理后台登录</div>
      <el-form :model="form" :rules="rules" ref="formRef" label-position="top" @keyup.enter="submit">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" placeholder="请输入用户名" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="form.password" type="password" show-password placeholder="请输入密码" @input="clearLoginError" />
        </el-form-item>
        <!-- P1-1：登录失败原因持久显示（不止 3 秒 toast，含剩余次数/锁定提示） -->
        <div v-if="loginError" class="login-error">{{ loginError }}</div>
        <el-button type="primary" class="login-btn" :loading="loading" @click="submit">登 录</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { ElMessage } from 'element-plus';
import request from '../../utils/request';
import { useUserStore } from '../../stores/user';

const router = useRouter();
const route = useRoute();
const userStore = useUserStore();

const form = reactive({ username: '', password: '' });
const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
};
const formRef = ref(null);
const loading = ref(false);
// P1-1：登录失败原因持久红字（含剩余次数/锁定提示），下次输入时清除
const loginError = ref('');

function clearLoginError() {
  loginError.value = '';
}

function submit() {
  formRef.value.validate((valid) => {
    if (!valid || loading.value) return;
    loading.value = true;
    loginError.value = '';
    request.post('/api/auth/login', { username: form.username, password: form.password })
      .then((data) => {
        userStore.setAuth(data.access_token, data.user, data.refresh_token, data.must_change_password);
        // 多租户：登录后写入项目列表并选定默认项目（后续请求自动带 X-Project-Id）
        userStore.setProjects(data.projects || [], (data.user && data.user.project_id) || '');
        ElMessage.success('登录成功');
        if (data.must_change_password) {
          router.push('/change-password');
        } else {
          router.push(route.query.redirect || '/dashboard');
        }
      })
      .catch((err) => {
        loginError.value = (err && err.message) || '登录失败，请稍后再试';
      })
      .finally(() => {
        loading.value = false;
      });
  });
}
</script>

<style scoped>
.login-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1e5fd0 0%, #3a7bd5 100%);
}
.login-card {
  width: 400px;
  padding: 12px 8px;
}
.login-title {
  text-align: center;
  font-size: 20px;
  font-weight: 700;
  color: #1a2634;
}
.login-sub {
  text-align: center;
  font-size: 14px;
  color: #5b6b7d;
  margin: 8px 0 24px;
}
.login-btn {
  width: 100%;
}
/* P1-1：登录失败持久红字 */
.login-error {
  color: #f56c6c;
  font-size: 13px;
  line-height: 1.5;
  margin: -8px 0 12px;
  word-break: break-all;
}
.login-tip {
  text-align: center;
  font-size: 12px;
  color: #5b6b7d;
  margin-top: 16px;
}
</style>
