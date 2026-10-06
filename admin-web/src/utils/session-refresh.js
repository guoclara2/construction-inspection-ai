// Dependency injection keeps session race behavior testable without a browser.
export function createSessionRefresh({ post, storage, getStore }) {
  let pending = null;
  return function refreshAccessToken() {
    if (pending) return pending;
    const refreshToken = storage.getItem('refreshToken');
    if (!refreshToken) return Promise.resolve(false);
    pending = post('/api/auth/refresh', { refresh_token: refreshToken }, { timeout: 10000 })
      .then(({ data: body }) => {
        // A logout or another login must win over an older in-flight response.
        if (storage.getItem('refreshToken') !== refreshToken) return !!storage.getItem('token');
        const data = body && body.code === 0 && body.data;
        if (!data || !data.access_token || !data.refresh_token) return false;
        getStore().setTokens(data.access_token, data.refresh_token);
        return true;
      })
      .catch(() => storage.getItem('refreshToken') !== refreshToken && !!storage.getItem('token'))
      .finally(() => { pending = null; });
    return pending;
  };
}
