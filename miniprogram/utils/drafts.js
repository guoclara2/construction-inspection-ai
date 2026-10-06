// Durable private drafts; account + project form the storage namespace.
function namespace() {
  const user = wx.getStorageSync('userInfo') || {};
  const project = wx.getStorageSync('projectId');
  if (!user.id || !project) throw new Error('请先登录并选择项目');
  return 'inspection-drafts:v1:' + user.id + ':' + project;
}
function uuid() { return Date.now().toString(36) + '-' + Math.random().toString(36).slice(2) + '-' + Math.random().toString(36).slice(2); }
function list() { return wx.getStorageSync(namespace()) || []; }
function save(id, data, expectedNamespace) {
  if (expectedNamespace !== namespace()) throw new Error('项目或账号已经切换，请重新进入草稿');
  const rows = list().filter((d) => d.id !== id);
  rows.unshift({ id, updatedAt: new Date().toISOString(), data });
  wx.setStorageSync(namespace(), rows);
}
function remove(id) {
  const rows = list(), target = rows.find((d) => d.id === id);
  wx.setStorageSync(namespace(), rows.filter((d) => d.id !== id));
  ((target && target.data.photos) || []).forEach((p) => {
    if (p.path) wx.removeSavedFile({ filePath: p.path, fail() {} });
  });
}
async function savePhoto(path) {
  const shotAt = new Date().toISOString();
  const location = await require('./api').getLocation();
  return new Promise((resolve, reject) => wx.saveFile({ tempFilePath: path,
    success(r) { resolve({ key: uuid(), path: r.savedFilePath, shotAt: shotAt, location: location, id: null }); },
    fail() { reject(new Error('本地照片保存失败，请检查剩余空间')); } }));
}
module.exports = { namespace, uuid, list, save, remove, savePhoto };
