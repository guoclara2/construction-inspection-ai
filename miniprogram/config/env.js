// 运行环境判定：
// - 开发版（含真机调试）→ BASE_URL 直连本机/局域网后端；
// - 体验版/正式版 + 云托管配置 → wx.cloud.callContainer（免域名、走微信内网）；
// - 体验版/正式版 + 自建域名 → BASE_URL HTTPS 直连（需备案并在小程序后台配置合法域名）。
const endpoints = require('./endpoints');
let version = 'release';
try { version = wx.getAccountInfoSync().miniProgram.envVersion || 'release'; } catch (e) {}
const IS_DEV = version === 'develop';
const CLOUD_RUN = endpoints.cloudRun || {};
const USE_CLOUD = !IS_DEV && endpoints.releaseMode === 'cloudrun';
const BASE_URL = (IS_DEV ? endpoints.develop : (version === 'trial' ? endpoints.trial : endpoints.release) || '').replace(/\/$/, '');
if (!IS_DEV && !['cloudrun', 'https'].includes(endpoints.releaseMode)) throw new Error('请显式配置 releaseMode');
if (USE_CLOUD && !(CLOUD_RUN.env && CLOUD_RUN.service)) throw new Error('云托管配置不完整');
// 上架前置校验：云托管或 HTTPS 域名二者必居其一，否则拒绝发布运行
if (!IS_DEV && !USE_CLOUD && (!/^https:\/\//.test(BASE_URL) || /example\.|localhost|127\.0\.0\.1/.test(BASE_URL))) {
  throw new Error('请先在 config/endpoints.js 配置云托管环境（cloudRun.env/service）或已备案的 HTTPS 服务域名');
}
module.exports = { IS_DEV, BASE_URL, USE_CLOUD, CLOUD_RUN };
