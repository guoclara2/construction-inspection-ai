// 工单中心：角色自适应 tab + 状态筛选 + 工单卡片列表 + 下拉刷新/上拉分页
const { get } = require('../../utils/api');
const { formatTime } = require('../../utils/format');
const { STATUS, STATUS_COLOR } = require('../../utils/dict');

// 状态筛选 chips（含已作废：管理员作废的工单可筛出查看）
const STATUS_FILTERS = [
  { value: '', label: '全部' },
  { value: 'pending', label: '待接收' },
  { value: 'accepted', label: '已接收' },
  { value: 'processing', label: '整改中' },
  { value: 'review', label: '待审核' },
  { value: 'closed', label: '已闭环' },
  { value: 'cancelled', label: '已作废' },
];

// 可读全部工单的角色（对齐后端 READ_ALL_ROLES，org_admin 经 is_org_admin 判断）
const READ_ALL_ROLES = ['reviewer', 'project_admin', 'org_admin', 'viewer'];

// 角色数组 → tab 配置（view 契约见总纲 5.4；P03 起登录响应为 roles 数组，一人可多角色）
function buildTabs(roles) {
  const tabs = [];
  if (roles.indexOf('rectifier') !== -1) {
    tabs.push({ view: 'my', label: '我的整改任务' });
  }
  if (roles.indexOf('reviewer') !== -1 && roles.indexOf('inspector') === -1) tabs.push({ view: 'review', label: '待我审核' });
  if (roles.indexOf('inspector') !== -1) {
    tabs.push({ view: 'review', label: '待我审核' });
    tabs.push({ view: 'mine', label: '我发起的' });
  }
  if (roles.some((r) => READ_ALL_ROLES.indexOf(r) !== -1)) {
    tabs.push({ view: 'all', label: '全部工单' });
  }
  if (!tabs.length) {
    // 无已知角色：兜底与后端 view=None 推导一致（my）
    tabs.push({ view: 'my', label: '我的整改任务' });
  }
  return tabs;
}

const PAGE_SIZE = 20;

Page({
  data: {
    roles: [],
    tabs: [],
    activeView: '',
    statusFilters: STATUS_FILTERS,
    activeStatus: '',
    orders: [],
    page: 1,
    total: 0,
    loading: false,
    noMore: false,
  },

  onShow() {
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().init('/pages/orders/index');
    }
    getApp().fetchUnread();
    const userInfo = wx.getStorageSync('userInfo') || {};
    const roles = (userInfo.roles || []).slice();
    if (userInfo.is_org_admin && roles.indexOf('org_admin') === -1) {
      roles.push('org_admin');
    }
    const tabs = buildTabs(roles);
    // 角色或 tab 失效时重置到首个 tab
    const views = tabs.map((t) => t.view);
    const activeView = views.indexOf(this.data.activeView) === -1
      ? (views[0] || '')
      : this.data.activeView;
    this.setData({ roles: roles, tabs: tabs, activeView: activeView });
    this.reload();
  },

  onTabTap(e) {
    const view = e.currentTarget.dataset.view;
    if (view === this.data.activeView) {
      return;
    }
    this.setData({ activeView: view });
    this.reload();
  },

  onStatusTap(e) {
    const value = e.currentTarget.dataset.value;
    if (value === this.data.activeStatus) {
      return;
    }
    this.setData({ activeStatus: value });
    this.reload();
  },

  reload() {
    this.setData({ page: 1, orders: [], noMore: false });
    this.loadMore();
  },

  loadMore() {
    if (this.data.loading || this.data.noMore || !this.data.activeView) {
      return;
    }
    const page = this;
    this.setData({ loading: true });
    const params = { view: this.data.activeView, page: this.data.page, page_size: PAGE_SIZE };
    if (this.data.activeStatus) {
      params.status = this.data.activeStatus;
    }
    get('/api/orders', params).then((data) => {
      // 卡片字段映射：状态中文/色、轮次、超期角标、对方姓名
      const isMyTask = page.data.activeView === 'my';
      const list = (data.list || []).map((o) => ({
        id: o.id,
        order_no: o.order_no,
        item_name: o.item_name,
        statusLabel: STATUS[o.status] || o.status,
        statusColor: STATUS_COLOR[o.status] || '#5B6B7D',
        roundLabel: o.round > 1 ? '第 ' + o.round + ' 轮' : '',
        overdue: o.overdue === true,
        counterpartLabel: isMyTask ? '发起人' : '责任人',
        counterpartName: isMyTask ? o.inspector_name : o.assignee_name,
        time: formatTime(o.created_at),
      }));
      const orders = page.data.orders.concat(list);
      page.setData({
        orders: orders,
        loading: false,
        total: data.total,
        page: data.page + 1,
        noMore: orders.length >= data.total,
      });
      wx.stopPullDownRefresh();
    }).catch(() => {
      page.setData({ loading: false });
      wx.stopPullDownRefresh();
    });
  },

  onPullDownRefresh() {
    this.reload();
  },

  onReachBottom() {
    this.loadMore();
  },

  goDetail(e) {
    wx.navigateTo({ url: '/pages/order-detail/index?id=' + e.currentTarget.dataset.id });
  },
});
