// 我的页：用户信息卡 + AI 功能说明 + 后端状态 + 切换账号（退出登录同义）
const { get } = require('../../utils/api');
const { ROLE, SPECIALTY } = require('../../utils/dict');

Page({
  data: {
    name: '',
    roleLabel: '',
    specialtyLabel: '',
    projectName: '',
    aiDescOpen: false,      // AI 功能说明展开区
    aiModeLabel: '检测中…', // real=已连接真实大模型 / mock=演示模式
    aiModeOk: false,
  },

  onShow() {
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().init('/pages/mine/index');
    }
    getApp().fetchUnread();
    const userInfo = wx.getStorageSync('userInfo') || {};
    // 角色改由当前项目成员关系承载（userInfo.roles）
    const roles = userInfo.roles || [];
    const projects = wx.getStorageSync('projects') || [];
    this.setData({
      name: userInfo.name || '',
      roleLabel: roles.map((r) => ROLE[r] || r).join('、') || (userInfo.is_org_admin ? '组织管理员' : ''),
      specialtyLabel: '',
      projectName: (wx.getStorageSync('projectInfo') || {}).name || userInfo.project_name || '',
      multiProject: projects.length > 1,
    });
    this.loadHealth();
  },

  // 切换项目：进入项目选择页（仅多项目用户可见入口）
  switchProject() {
    wx.navigateTo({ url: '/pages/project-select/index' });
  },

  loadHealth() {
    const page = this;
    get('/api/health').then((data) => {
      const isReal = data && data.ai_mode === 'real';
      page.setData({
        aiModeLabel: isReal ? '已连接真实大模型' : '演示模式',
        aiModeOk: data && data.status === 'ok',
      });
    }).catch(() => {
      page.setData({ aiModeLabel: '后端未连接' });
    });
  },

  toggleAiDesc() {
    this.setData({ aiDescOpen: !this.data.aiDescOpen });
  },

  // 切换账号（与退出登录同义，MVP 单用户态）：清缓存回登录页
  switchAccount() {
    wx.showModal({
      title: '切换账号',
      content: '确定退出当前账号并返回登录页？',
      success(res) {
        if (!res.confirm) {
          return;
        }
        wx.removeStorageSync('token');
        wx.removeStorageSync('refreshToken');
        wx.removeStorageSync('userInfo');
        wx.removeStorageSync('projectInfo');
        wx.removeStorageSync('projects');
        wx.removeStorageSync('projectId');
        wx.reLaunch({ url: '/pages/login/index' });
      },
    });
  },
});
