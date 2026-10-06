// 后端接入配置：
// - 微信云托管部署（推荐上架形态）：填 cloudRun.env + cloudRun.service，
//   releaseMode 选择 cloudrun 后，正式版/体验版走 wx.cloud.callContainer（内网免域名、免服务器域名配置），
//   trial/release 留空即可；
// - 自建服务器部署：releaseMode 选择 https，再填 trial/release 为已备案 HTTPS 域名（需在小程序后台配置 request 合法域名）。
module.exports = {
  releaseMode: 'cloudrun', // 显式选择 cloudrun 或 https，运行时与发布检查共用
  develop: 'http://192.168.1.5:18090', // 开发者工具模拟器与真机调试均用电脑局域网地址（ipconfig 实测 WLAN IP，随网段变化需更新）
  trial: '',                           // 体验版自建域名（云托管部署留空）
  release: '',                         // 正式版自建域名（云托管部署留空）
  cloudRun: {
    env: 'prod-d9goxpd0o79ae2c6a',      // 微信云托管环境 ID（云托管控制台-设置-全局变量/环境概览，形如 prod-xxxx）
    service: 'inspect',  // 云托管服务名称（云托管控制台-服务管理-服务列表）
  },
};
