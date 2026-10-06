// 自定义 tabBar：按权限过滤——无巡查权限（非 inspector 角色）不展示「智能巡查」tab
// 权限口径对齐后端 record:create（仅 inspector）：整改责任人/管理员落位到工单中心
const ALL_TABS = [
  {
    pagePath: '/pages/inspect/index',
    text: '智能巡查',
    icon: '/images/tabbar/inspect.png',
    selectedIcon: '/images/tabbar/inspect-active.png',
    needInspectPerm: true,
  },
  {
    pagePath: '/pages/orders/index',
    text: '工单中心',
    icon: '/images/tabbar/orders.png',
    selectedIcon: '/images/tabbar/orders-active.png',
  },
  {
    pagePath: '/pages/messages/index',
    text: '消息中心',
    icon: '/images/tabbar/messages.png',
    selectedIcon: '/images/tabbar/messages-active.png',
    badge: true,
  },
  {
    pagePath: '/pages/mine/index',
    text: '我的',
    icon: '/images/tabbar/mine.png',
    selectedIcon: '/images/tabbar/mine-active.png',
  },
];

Component({
  data: {
    selected: 0,
    color: '#5B6B7D',
    selectedColor: '#1E5FD0',
    list: ALL_TABS.slice(1), // 兜底：权限未知时先不显示巡查 tab，init 后再修正
    unread: 0,
  },

  methods: {
    // tab 页 onShow 调用：传入页面路径，重建可见 tab 并标记选中项
    init(pagePath) {
      const app = getApp();
      const canInspect = app.effectiveRoles().indexOf('inspector') !== -1;
      const list = ALL_TABS.filter((t) => !t.needInspectPerm || canInspect);
      let selected = 0;
      for (let i = 0; i < list.length; i++) {
        if (list[i].pagePath === pagePath) {
          selected = i;
          break;
        }
      }
      this.setData({ list: list, selected: selected, unread: app.globalData.unreadCount || 0 });
    },

    // 未读红点（App.fetchUnread → syncTabBadge 调用）
    setUnread(count) {
      this.setData({ unread: count || 0 });
    },

    switchTab(e) {
      const path = e.currentTarget.dataset.path;
      wx.switchTab({ url: path });
    },
  },
});
