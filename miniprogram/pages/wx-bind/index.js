// 手机号独立登录页；沿用原页面路径，不再获取微信绑定凭证
const { postRaw } = require('../../utils/api');

function saveAuthAndGo(data) {
  // 与登录页一致：写入项目列表与当前项目，再按权限落位
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
    wx.reLaunch({ url: '/pages/project-select/index' });
  } else {
    app.landHome();
  }
}

Page({
  data: {
    bindTicket: '',
    phone: '',
    smsCode: '',
    countdown: 0,
    ready: false,
    wxConfigError: '',  // 微信登录未配置提示（此时无法完成绑定）
    smsHint: '',        // dev 环境验证码提示（自动填入，无需短信）
  },

  onLoad() { this.setData({ready:true}); },
  onUnload() { clearInterval(this._timer); },

  onPhoneInput(e) { this.setData({ phone: e.detail.value }); },
  onCodeInput(e) { this.setData({ smsCode: e.detail.value }); },

  sendCode() {
    const phone = (this.data.phone || '').trim();
    if (!/^1\d{10}$/.test(phone)) {
      wx.showToast({ title: '请输入正确的手机号', icon: 'none' });
      return;
    }
    if (this.data.countdown > 0) return;
    const page = this;
    postRaw('/api/auth/sms-code', { phone: phone, scene: 'login' }).then((body) => {
      if (body.code === 0) {
        if (body.data && body.data.debug_code) {
          // dev 环境：后端返回调试验证码，自动填入并明确提示（未配置短信服务）
          page.setData({
            smsCode: body.data.debug_code,
            smsHint: '开发环境未配置短信服务：验证码 ' + body.data.debug_code + ' 已自动填入',
          });
        } else {
          page.setData({ smsHint: '验证码已发送至手机短信，5 分钟内有效' });
        }
        page.startCountdown();
      } else {
        // 兼容 429 等 HTTPException 响应体（detail.msg）与标准业务体（msg）
        const msg = (body.detail && body.detail.msg) || body.msg || '发送失败';
        wx.showToast({ title: msg, icon: 'none' });
      }
    }).catch(() => {});
  },

  startCountdown() {
    this.setData({ countdown: 60 });
    const page = this;
    clearInterval(this._timer);
    const timer = this._timer = setInterval(() => {
      const c = page.data.countdown - 1;
      if (c <= 0) {
        clearInterval(timer);
        page.setData({ countdown: 0 });
      } else {
        page.setData({ countdown: c });
      }
    }, 1000);
  },

  submit() {
    const phone = (this.data.phone || '').trim();
    const smsCode = (this.data.smsCode || '').trim();
    if (!phone || !smsCode) {
      wx.showToast({ title: '请填写手机号与验证码', icon: 'none' });
      return;
    }
    postRaw('/api/auth/phone-login', {
      phone: phone, sms_code: smsCode,
    }).then((body) => {
      if (body.code === 0) {
        saveAuthAndGo(body.data);
      } else {
        wx.showToast({ title: body.msg || '登录失败', icon: 'none' });
      }
    }).catch(() => {});
  },
});
