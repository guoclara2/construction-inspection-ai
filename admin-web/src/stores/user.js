// 用户状态：access/refresh token + userInfo 持久化 localStorage（总纲 B端契约）
import { defineStore } from 'pinia';

export const useUserStore = defineStore('user', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    refreshToken: localStorage.getItem('refreshToken') || '',
    userInfo: JSON.parse(localStorage.getItem('userInfo') || 'null'),
    mustChangePassword: localStorage.getItem('mustChangePassword') === '1',
    // 多租户：可进入的项目列表 + 当前项目 id
    projects: JSON.parse(localStorage.getItem('projects') || '[]'),
    projectId: localStorage.getItem('projectId') || '',
  }),
  getters: {
    currentProject: (state) => state.projects.find((p) => String(p.id) === String(state.projectId)) || null,
  },
  actions: {
    setAuth(token, user, refreshToken, mustChange) {
      this.token = token;
      this.userInfo = user;
      localStorage.setItem('token', token);
      localStorage.setItem('userInfo', JSON.stringify(user));
      if (refreshToken !== undefined && refreshToken !== null) {
        this.refreshToken = refreshToken;
        localStorage.setItem('refreshToken', refreshToken);
      }
      this.mustChangePassword = !!mustChange;
      localStorage.setItem('mustChangePassword', mustChange ? '1' : '0');
    },
    // 登录后写入项目列表并选定默认项目
    setProjects(projects, defaultId) {
      this.projects = projects || [];
      localStorage.setItem('projects', JSON.stringify(this.projects));
      let pid = defaultId;
      if (!pid) {
        const def = this.projects.find((p) => p.is_default) || this.projects[0];
        pid = def ? def.id : '';
      }
      this.setProjectId(pid);
    },
    setProjectId(projectId) {
      this.projectId = projectId ? String(projectId) : '';
      if (this.projectId) {
        localStorage.setItem('projectId', this.projectId);
      } else {
        localStorage.removeItem('projectId');
      }
    },
    // 仅刷新 access（refresh 轮换时可选带新 refresh）
    setTokens(token, refreshToken) {
      this.token = token;
      localStorage.setItem('token', token);
      if (refreshToken) {
        this.refreshToken = refreshToken;
        localStorage.setItem('refreshToken', refreshToken);
      }
    },
    clearMustChange() {
      this.mustChangePassword = false;
      localStorage.setItem('mustChangePassword', '0');
    },
    logout() {
      this.token = '';
      this.refreshToken = '';
      this.userInfo = null;
      this.mustChangePassword = false;
      this.projects = [];
      this.projectId = '';
      localStorage.removeItem('token');
      localStorage.removeItem('refreshToken');
      localStorage.removeItem('userInfo');
      localStorage.removeItem('mustChangePassword');
      localStorage.removeItem('projects');
      localStorage.removeItem('projectId');
    },
  },
});
