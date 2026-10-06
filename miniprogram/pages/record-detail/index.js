// 巡查记录详情：检查项快照 + 照片 + AI 判别结果 + 人工结论 + 关联工单
const { get, signedImageUrl } = require('../../utils/api');
const { CATEGORY, STATUS, VERDICT, STATUS_COLOR, SEVERITY_COLOR } = require('../../utils/dict');
const { evidenceMeta } = require('../../utils/format');

Page({
  data: {
    record: null,
    photos: [], locationText:'',
    photoFull: '',
    photoMeta: null,
    categoryLabel: '',
    aiDisplay: null,
    aiMode: '',
    verdictLabel: '',
    verdictClass: '',
    relatedOrder: null,
    relatedStatusLabel: '',
    relatedStatusColor: '',
  },

  previewAll(){wx.previewImage({urls:this.data.photos.map(p=>p.signedUrl)});},
  onLoad(options) {
    this.recordId = options.id;
    this.loadDetail();
  },

  onShow() {
    getApp().fetchUnread();
    // 从关联工单返回时刷新（工单状态可能已变化）
    if (this.recordId && this.data.record) {
      this.loadDetail();
    }
  },

  loadDetail() {
    const page = this;
    get('/api/inspection-records/' + this.recordId).then((r) => {
      const labels={building:'楼栋/区域',floor:'楼层',axis:'轴线',chainage:'桩号',equipment:'设备',measurement:'实测',annotation:'标注'};
      const loc = r.location || {};
      let locationText = (loc.custom || '').trim();
      if (!locationText) {
        locationText = Object.keys(loc).filter((k) => loc[k]).map((k) => (labels[k] || k) + ': ' + loc[k]).join('；');
      }
      page.setData({ locationText });
      Promise.all((r.photos||[]).map(async(p)=>Object.assign({},p,{signedUrl:await signedImageUrl(p.id)}))).then(photos=>page.setData({photos}));
      const ai = r.ai_result;
      let aiDisplay = null;
      if (ai) {
        const needReview = ai.has_defect === null || ai.status === 'unknown' || !(typeof ai.confidence === 'number') || ai.confidence < 0.6;
        let verdictText = '未发现异常';
        let verdictClass = 'success';
        if (needReview) {
          verdictText = '建议人工复核';
          verdictClass = 'warn';
        } else if (ai.has_defect) {
          verdictText = '存在异常';
          verdictClass = 'danger';
        }
        aiDisplay = {
          verdictText: verdictText,
          verdictClass: verdictClass,
          severity: ai.severity || '无',
          severityColor: SEVERITY_COLOR[ai.severity] || '#5B6B7D',
          confidencePct: Math.round((ai.confidence || 0) * 100),
          confidenceClass: needReview ? 'warn' : 'success',
          confidenceHint: needReview ? '建议人工复核' : '',
          defect_desc: ai.defect_desc || '',
          basis: ai.basis || '',
          suggestion: ai.suggestion || '',
        };
      }
      const ro = r.related_order;
      page.setData({
        record: r,
        photoFull: '',
        photoMeta: evidenceMeta(r.photo),
        categoryLabel: CATEGORY[r.defect_category] || r.defect_category,
        aiDisplay: aiDisplay,
        aiMode: r.ai_mode || '',
        verdictLabel: r.human_verdict ? VERDICT[r.human_verdict] : '待确认',
        verdictClass: r.human_verdict === 'normal' ? 'tag-success' : (r.human_verdict === 'abnormal' ? 'tag-danger' : 'tag-info'),
        relatedOrder: ro,
        relatedStatusLabel: ro ? STATUS[ro.status] : '',
        relatedStatusColor: ro ? (STATUS_COLOR[ro.status] || '#5B6B7D') : '',
      });
      // 照片短时效签名 URL（P0-3）：经 /signed-url 换取后展示，令牌不进 URL
      if (r.photo) {
        signedImageUrl(r.photo.id).then((url) => {
          page.setData({ photoFull: url });
        }).catch(() => {});
      }
    }).catch(() => {});
  },

  previewPhoto() {
    if (this.data.photoFull) {
      wx.previewImage({ urls: [this.data.photoFull] });
    }
  },

  goOrder() {
    if (this.data.relatedOrder) {
      wx.navigateTo({ url: '/pages/order-detail/index?id=' + this.data.relatedOrder.id });
    }
  },
});
