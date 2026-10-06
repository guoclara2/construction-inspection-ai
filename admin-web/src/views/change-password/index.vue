<template>
  <div class="cp-page">
    <el-card class="cp-card">
      <div class="cp-title">修改密码</div>
      <div class="cp-sub">首次登录或密码被重置后，需修改为仅本人知晓的强密码</div>
      <el-form :model="form" :rules="rules" ref="formRef" label-position="top">
        <el-form-item label="原密码 / 初始密码" prop="oldPassword">
          <el-input v-model="form.oldPassword" type="password" show-password placeholder="请输入原密码" />
        </el-form-item>
        <el-form-item label="新密码" prop="newPassword">
          <el-input v-model="form.newPassword" type="password" show-password
                    placeholder="≥10 位，含大小写字母/数字/符号至少三类" />
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirmPassword">
          <el-input v-model="form.confirmPassword" type="password" show-password placeholder="再次输入新密码" />
        </el-form-item>
        <el-button type="primary" class="cp-btn" :loading="loading" @click="submit">确认修改</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import request from '../../utils/request';
import { useUserStore } from '../../stores/user';

const router = useRouter();
const userStore = useUserStore();
const formRef = ref(null);
const loading = ref(false);

const form = reactive({ oldPassword: '', newPassword: '', confirmPassword: '' });
const rules = {
  oldPassword: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
  newPassword: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 10, message: '新密码至少 10 位', trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (rule, value, cb) => (value === form.newPassword ? cb() : cb(new Error('两次输入的新密码不一致'))),
      trigger: 'blur',
    },
  ],
};

function submit() {
  formRef.value.validate((valid) => {
    if (!valid || loading.value) return;
    loading.value = true;
    request.post('/api/auth/password', { old_password: form.oldPassword, new_password: form.newPassword })
      .then((data) => {
        // 后端下发新令牌，更新登录态并清除强制改密标记
        userStore.setAuth(data.access_token, data.user, data.refresh_token, false);
        ElMessage.success('密码修改成功');
        router.push('/dashboard');
      })
      .catch(() => {})
      .finally(() => { loading.value = false; });
  });
}
</script>

<style scoped>
.cp-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1e5fd0 0%, #3a7bd5 100%);
}
.cp-card { width: 420px; padding: 12px 8px; }
.cp-title { text-align: center; font-size: 20px; font-weight: 700; color: #1a2634; }
.cp-sub { text-align: center; font-size: 13px; color: #5b6b7d; margin: 8px 0 24px; }
.cp-btn { width: 100%; }
</style>
