// axios 实例：token 注入 + 401 自动 refresh 重放一次 + 403/429/500/1060 统一处理
import axios from 'axios';
import { ElMessage } from 'element-plus';
import router from '../router';
import { useUserStore } from '../stores/user';
import { createSessionRefresh } from './session-refresh';

const request = axios.create({
  baseURL: '',
  timeout: 30000,
});

function clearAuth() {
  useUserStore().logout();
}

function toLogin() {
  clearAuth();
  if (window.location.pathname !== '/login') {
    // P3-4：SPA 内跳转替代 location.href，避免与初始路由导航竞争导致 net::ERR_ABORTED
    router.replace('/login');
  }
}

const refreshAccessToken = createSessionRefresh({
  post: (...args) => axios.post(...args),
  storage: localStorage,
  getStore: useUserStore,
});

request.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = 'Bearer ' + token;
  }
  // 多租户：统一注入当前项目上下文（后端所有业务查询按此强制隔离）
  const projectId = localStorage.getItem('projectId');
  if (projectId) {
    config.headers['X-Project-Id'] = projectId;
  }
  return config;
});

request.interceptors.response.use(
  (response) => {
    const body = response.data;
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code === 0) {
        return body.data;
      }
      // 强制改密：跳改密页
      if (body.code === 1060) {
        if (window.location.pathname !== '/change-password') {
          window.location.href = '/change-password';
        }
        return Promise.reject(new Error(body.msg || '请先修改初始密码'));
      }
      // P2-2 契约：业务错误一律 HTTP 200 + code≠0；权限不足统一 HTTP 403（见下方错误拦截器）
      if (!response.config.silent) {
        ElMessage.error(body.msg || '请求失败');
      }
      return Promise.reject(Object.assign(new Error(body.msg || '请求失败'), { code: body.code }));
    }
    return body;
  },
  async (error) => {
    const resp = error.response;
    const original = error.config || {};
    // 401：尝试 refresh 并重放一次
    if (resp && resp.status === 401) {
      if (!original._retried) {
        original._retried = true;
        const currentToken = localStorage.getItem('token');
        // A delayed 401 for the previous token can reuse the already renewed session.
        const sentAuth = original.headers && original.headers.Authorization;
        const ok = (currentToken && sentAuth !== 'Bearer ' + currentToken) || await refreshAccessToken();
        if (ok) {
          original.headers = original.headers || {};
          original.headers.Authorization = 'Bearer ' + localStorage.getItem('token');
          return request(original);
        }
      }
      toLogin();
      return Promise.reject(new Error('登录已失效'));
    }
    // P2-2 契约：权限不足统一 HTTP 403 包裹体 {code:403, msg, data}
    if (resp && resp.status === 403) {
      const msg = (resp.data && (resp.data.msg || (resp.data.detail && resp.data.detail.msg))) || '无权限';
      if (!original.silent) ElMessage.error(msg);
      return Promise.reject(new Error(msg));
    }
    if (resp && resp.status === 429) {
      const msg = '操作过于频繁，请稍后再试';
      if (!original.silent) ElMessage.error(msg);
      return Promise.reject(new Error(msg));
    }
    if (resp && resp.status === 500) {
      const trace = resp.data && (resp.data.trace_id || (resp.data.detail && resp.data.detail.trace_id));
      const msg = trace ? `服务异常（trace: ${trace}）` : '服务异常';
      if (!original.silent) ElMessage.error(msg);
      return Promise.reject(new Error(msg));
    }
    const msg = (resp && resp.data && (
      resp.data.msg || (resp.data.detail && resp.data.detail.msg)
    )) || '网络异常，请检查后端服务';
    if (!original.silent) {
      ElMessage.error(msg);
    }
    return Promise.reject(Object.assign(new Error(msg), { status: resp && resp.status, code: resp && resp.data && resp.data.code }));
  },
);

export default request;

// 附件短时效签名 URL（P0-3）：经 /api/attachments/{id}/signed-url 换取 ?uid=&exp=&sig= 链接，
// 供 img 标签等无法携带请求头的场景使用；令牌不再进入 URL（服务器日志/浏览器历史/Referer 均不可见）。
export function signedImageUrl(path) {
  if (!path) return Promise.resolve('');
  if (typeof path === 'number' && Number.isSafeInteger(path) && path > 0) {
    path = `/api/attachments/${path}/file`;
  }
  const m = String(path).match(/^\/api\/attachments\/(\d+)\/file/);
  if (!m) return Promise.resolve(typeof path === 'string' ? path : '');
  return request.get(`/api/attachments/${m[1]}/signed-url`, { silent: true })
    .then((data) => (data && data.url) || '');
}
