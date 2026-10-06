// 统一请求工具：自动携带 access token、401 自动 refresh 重放一次、1060 强制改密拦截
// 双通道（config/endpoints.js 决定）：
//   - 微信云托管：wx.cloud.callContainer（内网免域名、免服务器域名配置、请求头自动注入 openid）
//   - 自建/开发：wx.request 直连 BASE_URL（开发工具模拟器本机回环；真机用电脑局域网 IP）
// callContainer 限制：请求体 ≤100KiB、超时上限 15s —— 大文件一律走云存储直传（upload 云通道）。
const { BASE_URL, USE_CLOUD, CLOUD_RUN } = require('../config/env');

function clearAuth() {
  wx.removeStorageSync('token');
  wx.removeStorageSync('refreshToken');
  wx.removeStorageSync('userInfo');
  wx.removeStorageSync('projectInfo');
  wx.removeStorageSync('projectId');
}

function gotoLogin(msg) {
  clearAuth();
  if (msg) {
    wx.showToast({ title: msg, icon: 'none' });
  }
  wx.reLaunch({ url: '/pages/login/index' });
}

// 底层请求原语：返回 Promise<{statusCode, data}>；云托管走 callContainer，否则 wx.request
function rawRequest(opts, _cloudRetried) {
  if (USE_CLOUD) {
    return wx.cloud.callContainer({
      config: { env: CLOUD_RUN.env },
      path: opts.url,
      method: opts.method || 'GET',
      data: opts.data,
      header: Object.assign({ 'X-WX-SERVICE': CLOUD_RUN.service }, opts.header || {}),
      timeout: Math.min(opts.timeout || 10000, 15000),
    }).then((res) => ({ statusCode: res.statusCode, data: res.data })).catch((err) => {
      // wx.cloud.init 异步未完成时偶发 "Cloud API isn't enabled"：等待 300ms 重试一次
      const msg = (err && err.errMsg) || String(err || '');
      if (!_cloudRetried && msg.indexOf("Cloud API isn't enabled") !== -1) {
        return new Promise((resolve) => {
          setTimeout(() => resolve(rawRequest(opts, true)), 300);
        });
      }
      throw err;
    });
  }
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE_URL + opts.url,
      method: opts.method || 'GET',
      data: opts.data,
      header: opts.header || {},
      timeout: opts.timeout || 10000, // 默认 10 秒：真机网络不通时快速失败（系统默认 60 秒过长）
      success(res) { resolve({ statusCode: res.statusCode, data: res.data }); },
      fail() { reject(new Error('网络异常')); },
    });
  });
}

// 用 refresh token 换新 access token；成功返回 true
let _refreshing = null;
function doRefresh() {
  if (_refreshing) {
    return _refreshing; // 并发请求共享同一次 refresh
  }
  const refreshToken = wx.getStorageSync('refreshToken');
  if (!refreshToken) {
    return Promise.resolve(false);
  }
  _refreshing = rawRequest({
    url: '/api/auth/refresh',
    method: 'POST',
    data: { refresh_token: refreshToken },
    header: { 'content-type': 'application/json' },
  }).then(({ statusCode, data }) => {
    const body = data || {};
    if (statusCode === 200 && body.code === 0 && body.data) {
      wx.setStorageSync('token', body.data.access_token);
      if (body.data.refresh_token) {
        wx.setStorageSync('refreshToken', body.data.refresh_token);
      }
      return true;
    }
    return false;
  }).catch(() => false).then((ok) => {
    _refreshing = null;
    return ok;
  });
  return _refreshing;
}

function request(options, _retried) {
  const url = options.url;
  const method = options.method || 'GET';
  const needAuth = options.needAuth !== false;
  const silent = options.silent === true; // 静默模式：失败不 toast（用于后台清理类请求）

  const header = Object.assign({ 'content-type': 'application/json' }, options.headers || {});
  if (needAuth) {
    const token = wx.getStorageSync('token');
    if (token) {
      header.Authorization = 'Bearer ' + token;
    }
    // 多租户：注入当前项目上下文（后端按此强制隔离）
    const projectId = wx.getStorageSync('projectId');
    if (projectId) {
      header['X-Project-Id'] = String(projectId);
    }
  }

  return rawRequest({ url, method, data: options.data, header, timeout: options.timeout })
    .then(({ statusCode, data }) => {
      const body = data || {};
      // 鉴权失败：尝试用 refresh 换新令牌并重放一次
      if (statusCode === 401 || body.code === 401) {
        if (needAuth && !_retried) {
          return doRefresh().then((ok) => {
            if (ok) {
              return request(options, true);
            }
            const msg = (body.detail && body.detail.msg) || body.msg || '登录已失效';
            gotoLogin(msg);
            throw new Error(msg);
          });
        }
        const msg = (body.detail && body.detail.msg) || body.msg || '登录已失效';
        gotoLogin(msg);
        throw new Error(msg);
      }
      // 强制改密拦截：跳改密页
      if (body.code === 1060) {
        wx.showToast({ title: body.msg || '请先修改初始密码', icon: 'none' });
        wx.reLaunch({ url: '/pages/change-password/index' });
        throw new Error(body.msg || '请先修改初始密码');
      }
      // P2-2 契约：权限不足统一 HTTP 403 包裹体（detail:{code:403,msg,data}），
      // 其余 HTTP 4xx 同走 detail 结构提示；业务错误一律 HTTP 200 + code≠0（下方分支）
      if (statusCode >= 400 && statusCode !== 200) {
        const emsg = (body.detail && body.detail.msg) || body.msg || '请求失败';
        if (!silent) {
          wx.showToast({ title: emsg, icon: 'none' });
        }
        throw new Error(emsg);
      }
      if (body.code === 0) {
        return body.data;
      }
      if (!silent) {
        wx.showToast({ title: body.msg || '请求失败', icon: 'none' });
      }
      throw new Error(body.msg || '请求失败');
    })
    .catch((err) => {
      // 网络层失败（callContainer reject / wx.request fail）：仅此分支提示网络异常
      if (err && (err.errMsg || err.message === '网络异常')) {
        if (!silent) {
          wx.showToast({ title: '网络异常，请检查网络或稍后重试', icon: 'none' });
        }
        throw new Error('网络异常');
      }
      throw err;
    });
}

function get(url, data) {
  return request({ url: url, method: 'GET', data: data });
}

function post(url, data, timeout, headers) {
  return request({ url: url, method: 'POST', data: data, timeout: timeout, headers: headers });
}

function del(url, silent) {
  return request({ url: url, method: 'DELETE', silent: silent });
}

// 免鉴权请求（登录/验证码等），返回完整响应体 {code,msg,data}
function postRaw(url, data) {
  return rawRequest({ url: url, method: 'POST', data: data, header: { 'content-type': 'application/json' } })
    .then(({ statusCode, data: body }) => {
      if (statusCode === 429) {
        return { code: 429, msg: '操作过于频繁，请稍后再试', data: null };
      }
      return body || {};
    })
    .catch(() => {
      wx.showToast({ title: '网络异常，请检查网络或稍后重试', icon: 'none' });
      throw new Error('网络异常');
    });
}

// 附件短时效签名 URL（P0-3）：经 /api/attachments/{id}/signed-url 换取展示链接，
// 供 image 标签/previewImage 等无法携带请求头的场景使用；令牌不进入 URL。
// 云托管对象存储形态：响应含 file_id（cloud://…），小程序 image/previewImage 原生支持；
// 自建部署：响应为相对路径 ?uid=&exp=&sig=，拼接 BASE_URL。
function signedImageUrl(attachmentId) {
  if (!attachmentId) {
    return Promise.resolve('');
  }
  return get('/api/attachments/' + attachmentId + '/signed-url').then((data) => {
    if (!data || !data.url) {
      return '';
    }
    if (data.file_id) {
      return data.file_id;
    }
    if (/^https?:\/\//.test(data.url)) {
      return data.url;
    }
    return BASE_URL + data.url;
  });
}

// 获取当前定位（gcj02）：失败容忍返回 null（EXIF 无 GPS 时作为客户端定位存证补充）
function getLocation() {
  return new Promise((resolve) => {
    wx.getLocation({
      type: 'gcj02',
      success(res) { resolve({ lat: res.latitude, lng: res.longitude, accuracy: res.accuracy }); },
      fail() { resolve(null); },
    });
  });
}

// 云存储直传对象键：日期分目录 + 随机文件名（不含敏感信息；原始文件不落业务路径）
function _cloudPath(filePath) {
  const ext = (filePath.match(/\.[a-zA-Z0-9]+$/) || ['.jpg'])[0];
  const d = new Date();
  const pad = (n) => (n < 10 ? '0' + n : '' + n);
  const rand = Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  return 'miniapp/' + d.getFullYear() + pad(d.getMonth() + 1) + '/' + rand + ext;
}

// 云托管上传通道：wx.cloud.uploadFile 直传对象存储 → POST /api/attachments/from-cloud 落库
// （后端按 fileID 下载校验 magic bytes/SHA256/EXIF 存证 → 水印 → 落库，原图不搬运）
function uploadViaCloud(filePath, opts) {
  return new Promise((resolve, reject) => {
    wx.cloud.uploadFile({
      cloudPath: _cloudPath(filePath),
      filePath: filePath,
      config: { env: CLOUD_RUN.env },
      success(res) { resolve(res.fileID); },
      fail() { reject(new Error('照片上传失败，请重试')); },
    });
  }).then((fileID) => {
    const location = (Object.prototype.hasOwnProperty.call(opts, 'location')
      ? Promise.resolve(opts.location) : getLocation());
    return location.then((loc) => {
      const payload = {
        file_id: fileID,
        file_name: (filePath.split('/').pop() || '').slice(0, 200),
        biz_type: opts.biz_type || 'record',
        source: opts.source || 'camera',
        shot_at: opts.shotAt || '',
        upload_key: opts.key || '',
        coordinate_system: 'gcj02', // 客户端声明拍摄时间（EXIF 缺失时的存证补充）
      };
      if (loc) {
        payload.client_lat = loc.lat;
        payload.client_lng = loc.lng;
        if (loc.accuracy != null) payload.accuracy = loc.accuracy;
      }
      return post('/api/attachments/from-cloud', payload, 15000);
    });
  });
}

// 证据附件上传：云托管走对象存储直传通道；自建/开发走 multipart 直传
// opts: { biz_type: 'record'|'order_feedback'|'order_extra', source: 'camera'|'album' }
function upload(filePath, opts, retried) {
  opts = opts || {};
  if (USE_CLOUD) {
    return uploadViaCloud(filePath, opts);
  }
  return new Promise((resolve, reject) => {
    const token = wx.getStorageSync('token');
    const header = {};
    if (token) {
      header.Authorization = 'Bearer ' + token;
    }
    // 多租户：上传也需带项目上下文
    const projectId = wx.getStorageSync('projectId');
    if (projectId) {
      header['X-Project-Id'] = String(projectId);
    }
    const formData = {
      biz_type: opts.biz_type || 'record',
      source: opts.source || 'camera',
      shot_at: opts.shotAt || '',
      upload_key: opts.key || '',
      coordinate_system: 'gcj02', // 客户端声明拍摄时间（EXIF 缺失时的存证补充）
    };
    (Object.prototype.hasOwnProperty.call(opts,'location') ? Promise.resolve(opts.location) : getLocation()).then((loc) => {
      if (loc) {
        formData.client_lat = String(loc.lat);
        formData.client_lng = String(loc.lng);
        if (loc.accuracy != null) formData.accuracy = String(loc.accuracy);
      }
      wx.uploadFile({
        url: BASE_URL + '/api/attachments',
        filePath: filePath,
        name: 'file',
        header: header,
        formData: formData,
        success(res) {
          let body = {};
          try {
            body = JSON.parse(res.data) || {};
          } catch (e) {
            body = {};
          }
          if (res.statusCode === 401 || body.code === 401) {
            if (!retried) {
              doRefresh().then((ok) => {
                if (ok) upload(filePath, opts, true).then(resolve).catch(reject);
                else { gotoLogin('登录已失效'); reject(new Error('登录已失效')); }
              });
              return;
            }
            gotoLogin('登录已失效');
            reject(new Error('登录已失效'));
            return;
          }
          if (res.statusCode === 429 || body.code === 429) {
            const msg = (body.detail && body.detail.msg) || body.msg || '上传过于频繁，请稍后再试';
            wx.showToast({ title: msg, icon: 'none' });
            reject(new Error(msg));
            return;
          }
          if (body.code === 0) {
            resolve(body.data);
            return;
          }
          wx.showToast({ title: body.msg || '上传失败', icon: 'none' });
          reject(new Error(body.msg || '上传失败'));
        },
        fail() {
          wx.showToast({ title: '网络异常，请检查后端服务', icon: 'none' });
          reject(new Error('网络异常'));
        },
      });
    });
  });
}

module.exports = {
  BASE_URL: BASE_URL,
  request: request,
  get: get,
  post: post,
  del: del,
  postRaw: postRaw,
  upload: upload,
  signedImageUrl: signedImageUrl,
  clearAuth: clearAuth,
  getLocation: getLocation,
};
