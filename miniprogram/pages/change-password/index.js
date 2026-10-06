// 改密页：首次登录强制改密 / 主动改密；改后后端下发新令牌，更新本地并进入主页
const { request } = require('../../utils/api');

Page({
  data: {
    oldPwd: '',
    newPwd: '',
    confirmPwd: '',
    submitting: false,
  },

  onOld(e) { this.setData({ oldPwd: e.detail.value }); },
  onNew(e) { this.setData({ newPwd: e.detail.value }); },
  onConfirm(e) { this.setData({ confirmPwd: e.detail.value }); },

  submit() {
    const { oldPwd, newPwd, confirmPwd } = this.data;
    if (!oldPwd || !newPwd) {
      wx.showToast({ title: '请填写完整', icon: 'none' });
      return;
    }
    if (newPwd.length < 10) {
      wx.showToast({ title: '新密码至少 10 位', icon: 'none' });
      return;
    }
    if (newPwd !== confirmPwd) {
      wx.showToast({ title: '两次输入的新密码不一致', icon: 'none' });
      return;
    }
    if (this.data.submitting) return;
    this.setData({ submitting: true });
    // 改密接口返回新令牌，直接更新本地登录态
    request({ url: '/api/auth/password', method: 'POST',
      data: { old_password: oldPwd, new_password: newPwd } })
      .then((data) => {
        wx.setStorageSync('token', data.access_token);
        wx.setStorageSync('refreshToken', data.refresh_token);
        wx.setStorageSync('userInfo', data.user);
        wx.showToast({ title: '密码修改成功', icon: 'success' });
        // 按权限落位：有巡查权限进智能巡查，否则进工单中心（对齐自定义 tabBar）
        setTimeout(() => { getApp().landHome(); }, 800);
      })
      .catch(() => {})
      .finally(() => { this.setData({ submitting: false }); });
  },
});
