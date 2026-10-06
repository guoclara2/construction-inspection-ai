// 消息中心：站内消息列表 / 单条已读+跳转 / 全部已读 / 未读角标联动
// P06 §2.4.1：页面停留时 60 秒轮询，onHide/onUnload 清除；§2.4.3 展示通道推送状态
const { get, post } = require('../../utils/api');
const { formatTime } = require('../../utils/format');

const PAGE_SIZE = 20;
const POLL_INTERVAL = 60000; // 60 秒页面级轮询（替代原全局 30 秒轮询）

Page({
  data: {
    messages: [],
    page: 1,
    total: 0,
    loading: false,
    noMore: false,
    marking: false,   // 全部已读防重复
  },
  _pollTimer: null,

  onLoad() {
    // 首次进入即启动轮询（onShow 对首个页面不触发轮询启动逻辑，统一在两个钩子都幂等启动）
    this.startPolling();
  },

  onShow() {
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().init('/pages/messages/index');
    }
    this.reload();
    getApp().fetchUnread();
    this.startPolling();
  },

  onHide() {
    this.stopPolling();
  },

  onUnload() {
    this.stopPolling();
  },

  startPolling() {
    if (this._pollTimer) {
      return;
    }
    const page = this;
    this._pollTimer = setInterval(() => {
      page.silentRefresh();
    }, POLL_INTERVAL);
  },

  stopPolling() {
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
  },

  // 轮询静默刷新：仅更新列表与角标，不重置分页状态到 loading 态
  silentRefresh() {
    const page = this;
    get('/api/messages', { page: 1, page_size: Math.max(page.data.messages.length, PAGE_SIZE) })
      .then((data) => {
        page.applyList(data, true);
        getApp().fetchUnread();
      }).catch(() => {});
  },

  reload() {
    this.setData({ page: 1, messages: [], noMore: false });
    this.loadMore();
  },

  applyList(data, replace) {
    const list = (data.list || []).map((m) => ({
      id: m.id,
      title: m.title,
      content: m.content,
      order_id: m.order_id,
      unread: m.read === false,
      pushed: m.pushed === true, // 同事件微信订阅消息已送达 →「已推送」
      stale: m.stale === true,   // P2-1：待办类消息关联工单已闭环 → 标题追加「已处理」
      time: formatTime(m.created_at),
    }));
    const messages = replace ? list : this.data.messages.concat(list);
    this.setData({
      messages: messages,
      loading: false,
      total: data.total,
      page: data.page + 1,
      noMore: messages.length >= data.total,
    });
    wx.stopPullDownRefresh();
  },

  loadMore() {
    if (this.data.loading || this.data.noMore) {
      return;
    }
    const page = this;
    this.setData({ loading: true });
    get('/api/messages', { page: this.data.page, page_size: PAGE_SIZE }).then((data) => {
      page.applyList(data, false);
    }).catch(() => {
      page.setData({ loading: false });
      wx.stopPullDownRefresh();
    });
  },

  // 单条点击：未读先标记已读 → 有 order_id 跳工单详情，否则仅刷新角标
  onItemTap(e) {
    const id = e.currentTarget.dataset.id;
    const msg = this.data.messages.filter((m) => m.id === id)[0];
    if (!msg) {
      return;
    }
    if (msg.unread) {
      this.setData({ ['messages[' + this.data.messages.indexOf(msg) + '].unread']: false });
      post('/api/messages/' + id + '/read').then(() => {
        getApp().fetchUnread();
      }).catch(() => {});
    }
    if (msg.order_id) {
      wx.navigateTo({ url: '/pages/order-detail/index?id=' + msg.order_id });
    }
  },

  // 全部已读：刷新列表与 TabBar 角标
  markAllRead() {
    if (this.data.marking) {
      return;
    }
    const page = this;
    this.setData({ marking: true });
    post('/api/messages/read-all').then(() => {
      page.setData({ marking: false });
      page.reload();
      getApp().fetchUnread();
    }).catch(() => {
      page.setData({ marking: false });
    });
  },

  onPullDownRefresh() {
    this.reload();
  },

  onReachBottom() {
    this.loadMore();
  },
});
