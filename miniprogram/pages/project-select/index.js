// 项目选择/切换页：多项目用户登录后或从「我的」进入，选定后换取 pid 更新的新令牌
const { post } = require('../../utils/api');
const { ROLE } = require('../../utils/dict');

Page({
  data: {
    projects: [],
    currentId: '',
  },

  onShow() {
    const raw = wx.getStorageSync('projects') || [];
    const currentId = String(wx.getStorageSync('projectId') || '');
    const projects = raw.map((p) => ({
      id: p.id,
      name: p.name,
      code: p.code,
      org_unit: p.org_unit,
      roleLabels: (p.roles || []).map((r) => ROLE[r] || r),
      isCurrent: String(p.id) === currentId,
    }));
    this.setData({ projects: projects, currentId: currentId });
  },

  onSelect(e) {
    const id = e.currentTarget.dataset.id;
    const projects = this.data.projects || [];
    const proj = projects.find((p) => String(p.id) === String(id));
    // 切换项目：换取 pid 更新后的新 access_token，写入本地上下文
    post('/api/projects/' + id + '/switch').then((data) => {
      wx.setStorageSync('token', data.access_token);
      wx.setStorageSync('projectId', id);
      const projectInfo = { id: id, name: (data.project && data.project.name) || (proj && proj.name) || '' };
      wx.setStorageSync('projectInfo', projectInfo);
      // 同步当前项目角色到 userInfo（tabBar 过滤/工单页 tab 均以 roles 为准）
      const rawProjects = wx.getStorageSync('projects') || [];
      const rawProj = rawProjects.find((p) => String(p.id) === String(id));
      const userInfo = wx.getStorageSync('userInfo') || {};
      userInfo.roles = (data.project && data.project.roles) || (rawProj && rawProj.roles) || [];
      wx.setStorageSync('userInfo', userInfo);
      const app = getApp();
      app.globalData.projectInfo = projectInfo;
      app.globalData.userInfo = userInfo;
      wx.showToast({ title: '已进入：' + projectInfo.name, icon: 'none' });
      // 按权限落位：有巡查权限进智能巡查，否则进工单中心
      app.landHome();
    }).catch(() => {});
  },
});
