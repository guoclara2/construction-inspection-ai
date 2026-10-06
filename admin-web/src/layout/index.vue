<template>
  <div class="layout">
    <!-- 左侧菜单 -->
    <el-aside width="220px" class="aside">
      <div class="brand">智能巡查助手</div>
      <el-menu :default-active="activeMenu" router class="menu">
        <el-menu-item index="/dashboard">
          <span>工作台</span>
        </el-menu-item>
        <el-menu-item index="/members">
          <span>组织与项目</span>
        </el-menu-item>
        <el-menu-item index="/knowledge">
          <span>知识库管理</span>
        </el-menu-item>
        <el-menu-item index="/supervise">
          <span>工单监督</span>
        </el-menu-item>
        <el-menu-item index="/notifications">
          <span>通知记录</span>
        </el-menu-item>
        <el-menu-item v-if="canSlaWrite" index="/operations"><span>巡查计划与运行情况</span></el-menu-item>
        <el-menu-item v-if="canSlaWrite" index="/sla">
          <span>SLA 时限配置</span>
        </el-menu-item>
        <el-menu-item v-if="canExport" index="/reports">
          <span>报表与导出</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <!-- 主体区 -->
    <el-container>
      <el-header height="60px" class="header">
        <div class="header-title">工程现场智能巡查助手 · 管理后台</div>
        <div class="header-right">
          <!-- 多租户：当前项目切换（切换后刷新当前页数据） -->
          <el-select
            v-if="projects.length"
            v-model="currentProjectId"
            size="small"
            class="project-select"
            placeholder="选择项目"
            aria-label="切换当前项目"
            popper-class="project-select-popper"
            :teleported="true"
            @change="onSwitchProject"
          >
            <el-option
              v-for="p in projects"
              :key="p.id"
              :label="p.name"
              :value="p.id"
            />
          </el-select>
          <span class="user-name">{{ userName }}</span>
          <el-button link type="danger" @click="logout">退出</el-button>
        </div>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import request from '../utils/request';
import { useUserStore } from '../stores/user';

const route = useRoute();
const router = useRouter();
const userStore = useUserStore();

const activeMenu = computed(() => route.path);
const userName = computed(() => (userStore.userInfo ? userStore.userInfo.name : ''));
const projects = computed(() => userStore.projects || []);
// SLA 配置入口：按当前项目上下文权限点门控（挂载时刷新 /api/auth/me）
const canSlaWrite = computed(() =>
  (userStore.userInfo && userStore.userInfo.perms ? userStore.userInfo.perms : []).includes('sla:write'));
// 报表导出入口：export 权限（project_admin / org_admin）门控
const canExport = computed(() =>
  (userStore.userInfo && userStore.userInfo.perms ? userStore.userInfo.perms : []).includes('export'));
const currentProjectId = ref(userStore.projectId ? Number(userStore.projectId) : '');

onMounted(() => {
  // 刷新当前用户信息（含 perms/roles/is_org_admin），消除项目切换后 localStorage 里的角色过期
  request.get('/api/auth/me').then((data) => {
    if (data && data.id) {
      userStore.userInfo = data;
      localStorage.setItem('userInfo', JSON.stringify(data));
    }
  }).catch(() => {});
});

async function onSwitchProject(projectId) {
  try {
    // 切换项目：换取 pid 更新后的新 access_token，写入本地并刷新当前页数据
    const data = await request.post(`/api/projects/${projectId}/switch`);
    userStore.setTokens(data.access_token);
    userStore.setProjectId(projectId);
    ElMessage.success('已切换项目：' + (data.project ? data.project.name : ''));
    // 刷新当前页，使数据在新项目上下文下重新加载
    window.location.reload();
  } catch (e) {
    // 切换失败：回退选择器到原项目
    currentProjectId.value = userStore.projectId ? Number(userStore.projectId) : '';
  }
}

function logout() {
  userStore.logout();
  router.push('/login');
}
</script>

<style scoped>
.layout {
  display: flex;
  height: 100%;
}
.aside {
  background: #ffffff;
  border-right: 1px solid #e4e7ed;
}
.brand {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 700;
  color: #1e5fd0;
  border-bottom: 1px solid #e4e7ed;
}
.menu {
  border-right: none;
}
.header {
  background: #ffffff;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.header-title {
  font-size: 16px;
  font-weight: 600;
  color: #1a2634;
  /* 小屏下让出空间给右侧项目切换与用户名，标题超长省略 */
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
  /* 右侧操作区不被压缩，避免项目下拉/用户名被挤变形 */
  flex-shrink: 0;
}
/* 项目切换下拉：固定宽度 + 选中项超长省略（完整名称在下拉面板中查看） */
.project-select {
  width: 200px;
  flex-shrink: 0;
}
.user-name {
  font-size: 14px;
  color: #5b6b7d;
  /* 账号名常规横向单行显示：不换行，超长省略 */
  white-space: nowrap;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  flex-shrink: 0;
}
.main {
  padding: 20px;
  overflow-y: auto;
}
</style>

<style>
/* 项目切换下拉面板（teleport 到 body 外层渲染，scoped 样式无法作用）：
   面板最小宽度略宽于选择框，保证长项目名在面板中完整展示 */
.project-select-popper {
  min-width: 260px !important;
  max-width: 420px;
}
</style>
