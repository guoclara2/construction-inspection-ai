// 微信订阅消息授权（P06 §2.4.2）：关键动作时请求授权并上报结果。
// 后端只对已授权用户发订阅消息；未配置模板 ID / 用户拒绝 → 静默降级，不阻断业务。
const { get, post } = require('./api');

// 已在本会话请求过授权的模板不再重复弹窗（微信本身也有频控，双保险）
let _asked = {};

/**
 * 请求订阅消息授权并上报。
 * @param {Array<string>} codes 需要授权的通知模板 code（如 ['ORDER_CREATED']）；
 *                             传空数组则请求全部可用模板
 * @returns {Promise<void>} 永不 reject（授权失败不影响业务）
 */
function requestSubscribe(codes) {
  return new Promise((resolve) => {
    if (!wx.getStorageSync('token')) {
      resolve();
      return;
    }
    get('/api/users/me/wx-subscribe', {}).then((data) => {
      const templates = (data && data.templates) || [];
      if (!templates.length) {
        resolve(); // 后端未配置微信模板 ID → 无可订阅项
        return;
      }
      let wanted = templates;
      if (codes && codes.length) {
        wanted = templates.filter((t) => codes.indexOf(t.code) >= 0);
      }
      // 过滤本会话已请求过的模板；按 wx_template_id 去重（多个通知模板可共用一个微信模板）
      wanted = wanted.filter((t) => !_asked[t.wx_template_id]);
      if (!wanted.length) {
        resolve();
        return;
      }
      const tmplIds = [];
      wanted.forEach((t) => {
        if (tmplIds.indexOf(t.wx_template_id) < 0) {
          tmplIds.push(t.wx_template_id);
        }
      });
      wx.requestSubscribeMessage({
        tmplIds: tmplIds,
        success(res) {
          // 上报结果：tmplId → accept/reject/ban 映射回所有共用该模板的 code
          const auths = {};
          wanted.forEach((t) => {
            const r = res[t.wx_template_id];
            if (r) {
              auths[t.code] = r;
              _asked[t.wx_template_id] = true;
            }
          });
          if (Object.keys(auths).length) {
            post('/api/users/me/wx-subscribe', { auths: auths }).catch(() => {});
          }
          resolve();
        },
        fail() {
          resolve(); // 用户关闭主开关/接口不可用：静默降级
        },
      });
    }).catch(() => resolve());
  });
}

module.exports = { requestSubscribe: requestSubscribe };
