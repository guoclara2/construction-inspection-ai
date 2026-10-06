// 登录页：微信一键登录（主）+ 手机号验证码独立登录入口；测试账号仅 dev 环境渲染
// dev 判定含真机调试（开发版 envVersion=develop），真机联调同样可直点测试账号
const { get, post, postRaw, request } = require('../../utils/api');
const { ROLE, SPECIALTY } = require('../../utils/dict');
const { IS_DEV } = require('../../config/env');

function saveAuthAndGo(data) {
  // 多租户：写入项目列表与默认项目，后续请求自动带 X-Project-Id
  const projects = data.projects || [];
  const defaultId = (data.user && data.user.project_id) || (projects[0] && projects[0].id) || '';
  const cur = projects.find((p) => p.id === defaultId) || projects[0] || null;
  const projectInfo = cur ? { id: cur.id, name: cur.name } : { id: data.user.project_id, name: data.user.project_name };
  wx.setStorageSync('token', data.access_token);
  wx.setStorageSync('refreshToken', data.refresh_token);
  wx.setStorageSync('userInfo', data.user);
  wx.setStorageSync('projects', projects);
  if (defaultId) {
    wx.setStorageSync('projectId', defaultId);
  }
  wx.setStorageSync('projectInfo', projectInfo);
  const app = getApp();
  app.globalData.userInfo = data.user;
  app.globalData.projectInfo = projectInfo;
  if (data.must_change_password) {
    wx.reLaunch({ url: '/pages/change-password/index' });
  } else if (projects.length > 1) {
    // 多个项目：进入项目选择页
    wx.reLaunch({ url: '/pages/project-select/index' });
  } else {
    // 按权限落位：有巡查权限进智能巡查，否则进工单中心（对齐自定义 tabBar）
    app.landHome();
  }
}

Page({
  data: {
    isDev: IS_DEV,
    accounts: [],
    loading: true,
    accountsError: '',   // 测试账号加载失败原因（真机调试配置指引）
    wxLogging: false,    // 微信登录进行中（按钮反馈）
  },

  onShow() {
    if (wx.getStorageSync('token')) {
      getApp().landHome();
      return;
    }
    if (IS_DEV) {
      this.loadAccounts();
    } else {
      this.setData({ loading: false });
    }
  },

  loadAccounts() {
    const page = this;
    // silent：失败不弹全局 toast，用页面内提示给出真机调试配置指引
    request({ url: '/api/auth/dev-accounts', silent: true }).then((list) => {
      const accounts = (list || []).map((u) => ({
        user_id: u.user_id,
        name: u.name,
        // 角色改由成员关系承载：展示默认项目角色列表（组织管理员额外标注）
        roleLabel: (u.roles || []).map((r) => ROLE[r] || r).join('、') || (u.is_org_admin ? '组织管理员' : ''),
        specialtyLabel: '',
      }));
      page.setData({ accounts: accounts, loading: false, accountsError: '' });
    }).catch((err) => {
      // 区分两类失败：网络不通（请求未达后端）vs 配置拒绝（404：三重防护未满足）
      const msg = (err && err.message) || '';
      let hint;
      if (msg.indexOf('网络异常') !== -1) {
        hint = '无法连接后端服务：请确认 ① 手机与电脑连接同一 Wi-Fi；② 后端已启动（uvicorn app.main:app --host 0.0.0.0 --port 18090）；' +
          '③ Windows 防火墙已放行 18090 端口（首次启动会弹窗，选"允许访问"）；' +
          '④ 真机需把 config/endpoints.js 的 develop 地址改为电脑局域网地址（如 http://192.168.x.x:18090，ipconfig 查看电脑实际 IP）';
      } else {
        hint = '测试账号不可用：请确认后端 APP_ENV=dev、DEV_LOGIN_ENABLED=true，' +
          '且手机 IP 已加入 DEV_LOGIN_ALLOWED_IPS 白名单（当前网段如 192.168.1.*），修改 backend/.env 后需重启后端';
      }
      page.setData({ loading: false, accountsError: hint });
    });
  },

  // 开发模式：点测试账号直接登录（dev-login 仅 dev 环境注册）
  onAccountTap(e) {
    const userId = e.currentTarget.dataset.id;
    if (this.data.wxLogging) {
      return;
    }
    this.setData({ wxLogging: true });
    const page = this;
    post('/api/auth/dev-login', { user_id: userId }).then((data) => {
      saveAuthAndGo(data);
    }).catch(() => {
      page.setData({ wxLogging: false });
    });
  },

  // 微信一键登录：新老用户均直接登录；4001 未配置
  onWxLogin() {
    if (this.data.wxLogging) {
      return;
    }
    const page = this;
    this.setData({ wxLogging: true });
    wx.showLoading({ title: '微信登录中…', mask: true });
    wx.login({
      success(res) {
        postRaw('/api/auth/wx-login', { code: res.code }).then((body) => {
          wx.hideLoading();
          page.setData({ wxLogging: false });
          if (body.code === 0) {
            saveAuthAndGo(body.data);
          } else {
            wx.showToast({ title: body.msg || '微信登录失败', icon: 'none' });
          }
        }).catch(() => {
          wx.hideLoading();
          page.setData({ wxLogging: false });
        });
      },
      fail() {
        wx.hideLoading();
        page.setData({ wxLogging: false });
        wx.showToast({ title: '微信登录失败，请重试', icon: 'none' });
      },
    });
  },

  // 手机号验证码独立入口，不依赖微信登录
  onPhoneLogin() {
    wx.navigateTo({ url: '/pages/wx-bind/index' });
  },
});
