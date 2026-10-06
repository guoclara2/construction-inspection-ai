const assert = require('node:assert/strict');
const { isIP } = require('node:net');
function validateRelease(endpoints, config) {
  assert(['cloudrun', 'https'].includes(endpoints.releaseMode), 'releaseMode 必须显式选择 cloudrun 或 https');
  if (endpoints.releaseMode === 'cloudrun') {
    const cloud = endpoints.cloudRun || {};
    for (const key of ['env', 'service']) {
      assert(typeof cloud[key] === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9_-]*$/.test(cloud[key]) && !/example|placeholder|your[-_]|待填写/i.test(cloud[key]), '请配置真实 cloudRun.' + key);
    }
  } else {
    for (const env of ['trial', 'release']) {
      const u = new URL(endpoints[env]);
      const host = u.hostname.replace(/^\[|\]$/g, '');
      assert(u.protocol === 'https:' && !u.username && !u.password && !u.search && !u.hash && !isIP(host) && host.includes('.') && !/(^|\.)(localhost|example|invalid|test|local)(\.|$)/i.test(host), env + ' 必须使用真实公网 HTTPS 域名，不能包含凭据或查询参数');
    }
  }
  assert(/^wx[0-9a-f]{16}$/.test(config.appid), '必须配置真实 AppID');
  assert(config.setting && config.setting.urlCheck === true, '发布必须启用域名校验');
  return endpoints.releaseMode;
}
module.exports = { validateRelease };
if (require.main === module) {
  const mode = validateRelease(require('../config/endpoints'), require('../project.config.json'));
  console.log('发布静态配置检查通过：' + mode + '；仍需实际服务、微信平台配置及真机验收');
}
