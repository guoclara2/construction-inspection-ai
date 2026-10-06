// 应用入口：登录态检查 + 未读消息红点（进入 App / 切前台时拉取，不做后台轮询）
// P06 §2.4.1：去掉 30 秒无脑轮询；页面级 60 秒轮询由消息中心页自持并在隐藏时清除
const { get } = require('./utils/api');
const { IS_DEV, USE_CLOUD, CLOUD_RUN } = require('./config/env');

App({
  globalData: { userInfo: null, projectInfo: null, unreadCount: 0 },

  onLaunch() {
    // 云托管通道：尽早初始化 wx.cloud（callContainer/uploadFile 依赖）。
    // init 异步生效，偶发 "Cloud API isn't enabled" 由 utils/api.js 兜底重试。
    if (!IS_DEV && USE_CLOUD && wx.cloud) {
      wx.cloud.init({ env: CLOUD_RUN.env });
    }
    if (!wx.getStorageSync('token')) {
      wx.reLaunch({ url: '/pages/login/index' });
    }
  },

  onShow() {
    // 切前台即时刷新一次未读数（下拉刷新由各页面触发）
    this.fetchUnread();
  },

  // 当前用户有效角色（含组织管理员附加 org_admin），与后端 effective_roles 口径一致
  effectiveRoles() {
    const userInfo = wx.getStorageSync('userInfo') || {};
    const roles = (userInfo.roles || []).slice();
    if (userInfo.is_org_admin && roles.indexOf('org_admin') === -1) {
      roles.push('org_admin');
    }
    return roles;
  },

  // 登录/切项目后的落位页：有巡查权限（inspector）进智能巡查，否则进工单中心
  // 对应自定义 tabBar：无 record:create 权限不展示「智能巡查」tab
  landHome() {
    const canInspect = this.effectiveRoles().indexOf('inspector') !== -1;
    wx.reLaunch({ url: canInspect ? '/pages/inspect/index' : '/pages/orders/index' });
  },

  // 未读消息红点：存 globalData 并同步到自定义 tabBar（wx.setTabBarBadge 对 custom tabBar 不生效）
  fetchUnread() {
    if (!wx.getStorageSync('token')) {
      return;
    }
    const app = this;
    get('/api/messages/unread-count').then((data) => {
      const count = (data && data.count) || 0;
      app.globalData.unreadCount = count;
      app.syncTabBadge();
    }).catch(() => {
      // 静默失败：不打断页面
    });
  },

  // 把未读数同步到当前页面的自定义 tabBar 实例（tab 页 onShow 时也会自行读取）
  syncTabBadge() {
    const pages = getCurrentPages();
    const top = pages.length ? pages[pages.length - 1] : null;
    if (top && typeof top.getTabBar === 'function') {
      const bar = top.getTabBar();
      if (bar && typeof bar.setUnread === 'function') {
        bar.setUnread(this.globalData.unreadCount);
      }
    }
  },
});
