// 智能巡查首页：项目信息条 + 功能入口 + 最近巡查记录 5 条
const { get } = require('../../utils/api');
const { TYPE, PHASE } = require('../../utils/dict');
const { formatTime } = require('../../utils/format');

Page({
  data: {
    projectName: '',
    typeLabel: '',
    phaseLabel: '',
    records: [],
    multiProject: false,
  },

  onShow() {
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().init('/pages/inspect/index');
    }
    getApp().fetchUnread();
    this.setData({ multiProject: (wx.getStorageSync('projects') || []).length > 1 });
    this.loadProject();
    this.loadRecords();
  },

  // 多项目用户：点顶部项目条切换项目
  switchProject() {
    if ((wx.getStorageSync('projects') || []).length > 1) {
      wx.navigateTo({ url: '/pages/project-select/index' });
    }
  },

  loadProject() {
    // 先用本地缓存即时渲染，再拉最新（管理端可能修改类型/阶段）
    const cached = wx.getStorageSync('projectInfo') || {};
    this.setData({
      projectName: cached.name || '',
      typeLabel: TYPE[cached.project_type] || cached.project_type || '',
      phaseLabel: PHASE[cached.phase] || cached.phase || '',
    });
    const page = this;
    get('/api/project').then((p) => {
      wx.setStorageSync('projectInfo', p);
      getApp().globalData.projectInfo = p;
      page.setData({
        projectName: p.name,
        typeLabel: TYPE[p.project_type] || p.project_type,
        phaseLabel: PHASE[p.phase] || p.phase,
      });
    }).catch(() => {});
  },

  loadRecords() {
    const page = this;
    get('/api/inspection-records', { page: 1, page_size: 5 }).then((data) => {
      const records = (data.list || []).map((r) => ({
        id: r.id,
        item_name: r.item_name,
        time: formatTime(r.created_at),
        aiLabel: r.ai_has_defect === null || r.ai_has_defect === undefined ? 'AI:未判定' : (r.ai_has_defect ? 'AI:异常' : 'AI:正常'),
        aiDefect: r.ai_has_defect === true,
        humanLabel: r.human_verdict ? (r.human_verdict === 'abnormal' ? '异常' : '正常') : '待确认',
        humanVerdict: r.human_verdict,
      }));
      page.setData({ records: records });
    }).catch(() => {});
  },

  goInquiry() {
    wx.navigateTo({ url: '/pages/inspect/inquiry' });
  },

  goForm() {
    wx.navigateTo({ url: '/pages/inspect/form' });
  },

  goRecordDetail(e) {
    wx.navigateTo({ url: '/pages/record-detail/index?id=' + e.currentTarget.dataset.id });
  },
});
