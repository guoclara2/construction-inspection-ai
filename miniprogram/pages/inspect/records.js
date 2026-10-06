// 巡查记录列表：状态筛选 chips + 下拉刷新 + 上拉分页
const { get } = require('../../utils/api');
const { formatTime } = require('../../utils/format');

const FILTERS = [
  { value: '', label: '全部' },
  { value: 'none', label: '待确认' },
  { value: 'normal', label: '正常' },
  { value: 'abnormal', label: '异常' },
];
const PAGE_SIZE = 20;

Page({
  data: {
    filters: FILTERS,
    activeFilter: '',
    records: [],
    page: 1,
    total: 0,
    loading: false,
    noMore: false,
  },

  onShow() {
    getApp().fetchUnread();
    this.reload();
  },

  onFilterTap(e) {
    const value = e.currentTarget.dataset.value;
    if (value === this.data.activeFilter) {
      return;
    }
    this.setData({ activeFilter: value });
    this.reload();
  },

  reload() {
    this.setData({ page: 1, records: [], noMore: false });
    this.loadMore();
  },

  loadMore() {
    if (this.data.loading || this.data.noMore) {
      return;
    }
    const page = this;
    this.setData({ loading: true });
    const params = { page: this.data.page, page_size: PAGE_SIZE };
    if (this.data.activeFilter) {
      params.verdict = this.data.activeFilter;
    }
    get('/api/inspection-records', params).then((data) => {
      const list = (data.list || []).map((r) => ({
        id: r.id,
        item_name: r.item_name,
        time: formatTime(r.created_at),
        aiLabel: r.ai_has_defect === null || r.ai_has_defect === undefined ? 'AI:未判定' : (r.ai_has_defect ? 'AI:异常' : 'AI:正常'),
        aiDefect: r.ai_has_defect === true,
        humanLabel: r.human_verdict ? (r.human_verdict === 'abnormal' ? '异常' : '正常') : '待确认',
        humanVerdict: r.human_verdict,
      }));
      const records = page.data.records.concat(list);
      page.setData({
        records: records,
        loading: false,
        total: data.total,
        page: data.page + 1,
        noMore: records.length >= data.total,
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
    wx.navigateTo({ url: '/pages/record-detail/index?id=' + e.currentTarget.dataset.id });
  },
});
