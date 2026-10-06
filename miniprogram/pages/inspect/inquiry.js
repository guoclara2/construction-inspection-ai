// 智能问询：类型+阶段 chips（必选）→ AI 事项推荐，聊天气泡多轮布局
const { post } = require('../../utils/api');
const { TYPE, PHASE, RISK, RISK_COLOR } = require('../../utils/dict');

const TYPE_KEYS = ['building', 'road', 'tunnel', 'landscape'];
const PHASE_KEYS = ['foundation', 'structure', 'mep', 'decoration'];

Page({
  data: {
    typeOptions: [],
    phaseOptions: [],
    projectType: '',
    phase: '',
    text: '',
    chat: [],   // [{role:'user'|'system', summary|loading|error|items}]
    submitting: false,
  },

  onLoad() {
    this.setData({
      typeOptions: TYPE_KEYS.map((v) => ({ value: v, label: TYPE[v] })),
      phaseOptions: PHASE_KEYS.map((v) => ({ value: v, label: PHASE[v] })),
    });
  },

  onShow() {
    getApp().fetchUnread();
  },

  onTypeTap(e) {
    this.setData({ projectType: e.currentTarget.dataset.value });
  },

  onPhaseTap(e) {
    this.setData({ phase: e.currentTarget.dataset.value });
  },

  onTextInput(e) {
    this.setData({ text: e.detail.value });
  },

  submit() {
    const projectType = this.data.projectType;
    const phase = this.data.phase;
    if (!projectType || !phase || this.data.submitting) {
      return;
    }
    const text = this.data.text.trim();
    const chat = this.data.chat.concat([
      { role: 'user', summary: TYPE[projectType] + ' · ' + PHASE[phase] + (text ? '：' + text : '') },
      { role: 'system', loading: true, items: [], error: false },
    ]);
    const sysIndex = chat.length - 1;
    this.setData({ chat: chat, submitting: true, text: '' });
    const page = this;
    post('/api/inspection-items/recommend', {
      project_type: projectType,
      phase: phase,
      text: text || null,
    }).then((list) => {
      const items = (list || []).map((r) => ({
        id: r.item.id,
        name: r.item.name,
        check_points: r.item.check_points || [],
        basis: r.item.basis,
        riskLabel: RISK[r.item.risk_level] || r.item.risk_level,
        riskColor: RISK_COLOR[r.item.risk_level] || '#5B6B7D',
        reason: r.reason,
      }));
      page.setData({
        ['chat[' + sysIndex + '].loading']: false,
        ['chat[' + sysIndex + '].items']: items,
        submitting: false,
      });
    }).catch(() => {
      page.setData({
        ['chat[' + sysIndex + '].loading']: false,
        ['chat[' + sysIndex + '].error']: true,
        submitting: false,
      });
    });
  },

  goForm(e) {
    wx.navigateTo({ url: '/pages/inspect/form?item_id=' + e.currentTarget.dataset.id });
  },
});
