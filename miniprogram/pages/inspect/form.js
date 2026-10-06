// 巡查填报：检查项选择 → 拍照上传 → 一键 AI 判别 → 人工确认 → 工单草稿指派
// 注意：不可解构出顶层 const upload——开发者工具沙箱包装函数含同名参数，会导致 SyntaxError（模块无法注册、页面白屏）
const { get, post, del, upload: uploadFile, signedImageUrl } = require('../../utils/api');
const { SPECIALTY, SEVERITY_COLOR } = require('../../utils/dict');
const { requestSubscribe } = require('../../utils/subscribe');

const drafts = require('../../utils/drafts');

const DRAFT_FIELDS = ['problem_location', 'rectify_requirement', 'basis'];

Page({
  data: {
    photos: [], location: {}, taskId: null, jobStage: '',
    item: null,             // 选定检查项
    keyword: '',
    searchResults: [],      // 检查项搜索下拉
    searching: false,
    attachmentId: null,     // 已上传证据附件 id（先传后绑，P04 §2.5.1）
    photoFull: '',          // 显示用完整 URL（鉴权）
    uploading: false,
    recordId: null,
    analyzing: false,
    analyzeFailed: false,
    aiResult: null,
    aiDisplay: null,
    aiActionable: true,     // AI 判别可采信（P0-4）：false 时草稿不预填 AI 文本、问题定位/整改要求必填
    mode: '',
    slaHours: null,
    draftReady: false,
    verdict: '',
    note: '',
    draft: { problem_location: '', rectify_requirement: '', basis: '' },
    draftOriginal: null,    // 预填初值，用于识别用户是否修改
    assigneeGroups: [],
    assigneeId: null,
    submitting: false,
  },

  onLoad(options) {
    this._namespace = drafts.namespace();
    this._draftId = (options && options.draft_id) || drafts.uuid();
    const saved = drafts.list().find((d) => d.id === this._draftId);
    if (saved) this.setData(Object.assign({}, saved.data, {analyzing:false,uploading:false,submitting:false}));
    else {
      this.setData({taskId: options && options.task_id ? Number(options.task_id) : null});
      if (options && options.item_id) this.loadItemById(options.item_id);
    }
    this._originalSetData = this.setData.bind(this);
    this.setData = (value, callback) => {
      this._originalSetData(value, callback);
      this.persist();
    };
    this.persist();
  },
  persist() {
    if (this._finished) return;
    try { drafts.save(this._draftId, this.data, this._namespace); }
    catch (e) { wx.showToast({title:e.message || '草稿保存失败，请检查存储空间',icon:'none'}); }
  },
  onShow() {
    getApp().fetchUnread();
    if (this.data.recordId) {
      get('/api/inspection-records/' + this.data.recordId).then((r) => {
        if (r.human_verdict) {
          this._finished = true; drafts.remove(this._draftId);
          wx.redirectTo({url:'/pages/record-detail/index?id='+r.id}); return;
        }
        if (this.data.jobSubmitted) this.pollJob();
        else if (r.ai_result) this.showResult(r.ai_result,r.ai_mode);
      }).catch(() => {});
    }
  },
  onHide() { clearTimeout(this._pollTimer); this.persist(); },
  onUnload() { clearTimeout(this._pollTimer); clearTimeout(this._searchTimer); this.persist(); },
  onLocationInput(e) { if (!this.data.recordId) this.setData({ 'location.custom': e.detail.value }); },
  async syncPhotos() {
    if (this._namespace !== drafts.namespace()) throw new Error('项目或账号已经切换');
    this.setData({uploading:true});
    try {
      const photos = this.data.photos.slice();
      for (let i=0; i<photos.length; i++) {
        if (!photos[i].id) {
          const att = await uploadFile(photos[i].path,{biz_type:'record',source:'camera',shotAt:photos[i].shotAt,key:photos[i].key,location:photos[i].location});
          photos[i] = Object.assign({},photos[i],{id:att.id,reasons:(att.evidence || {}).reasons || []});
          this.setData({photos:photos.slice(),attachmentId:photos[0].id,photoFull:photos[0].path});
        }
      }
    } finally { this.setData({uploading:false}); }
  },
  retryUploads() { this.syncPhotos().catch(() => {}); },
  async ensureRecord() {
    if (this.data.recordId) return {id:this.data.recordId};
    await this.syncPhotos();
    if (!this.data.createPayload) this.setData({createPayload:{item_id:this.data.item.id,
      attachment_id:this.data.attachmentId,attachment_ids:this.data.photos.map((p)=>p.id),
      request_key:this._draftId,location:this.data.location,task_id:this.data.taskId}});
    const r = await post('/api/inspection-records',this.data.createPayload);
    this.setData({recordId:r.id}); return r;
  },
  async pollJob() {
    clearTimeout(this._pollTimer);
    try {
      const job=await get('/api/inspection-records/'+this.data.recordId+'/analysis-job');
      this.setData({jobStage:job.stage});
      if(job.status==='done') { this.showResult(job.record.ai_result,job.record.ai_mode); return; }
      if(job.status==='failed'||job.status==='cancelled') { this.setData({analyzing:false,analyzeFailed:true,jobStage:job.error || job.stage}); return; }
      this._pollTimer=setTimeout(()=>this.pollJob(),3000);
    } catch(e) { this.setData({analyzing:false,jobStage:'连接中断，任务保留；重新进入草稿可恢复'}); }
  },
  showResult(ai,mode) {
    this.setData(Object.assign({analyzing:false,aiResult:ai,mode:mode,jobSubmitted:false,analyzeFailed:!!(ai && ai.retryable),
      aiActionable:!!ai && typeof ai.has_defect==='boolean' && ai.confidence>=0.6},this.buildAiDisplay(ai)));
  },

  // ---------- 检查项选择 ----------
  loadItemById(id) {
    const page = this;
    get('/api/inspection-items/'+Number(id)).then((found) => {
      if (found) {
        page.setData({ item: found });
      } else {
        wx.showToast({ title: '检查项不存在或已停用', icon: 'none' });
      }
    }).catch(() => {});
  },

  onKeywordInput(e) {
    const keyword = e.detail.value;
    this.setData({ keyword: keyword });
    if (this._searchTimer) {
      clearTimeout(this._searchTimer);
    }
    if (!keyword.trim()) {
      this.setData({ searchResults: [] });
      return;
    }
    const page = this;
    this._searchTimer = setTimeout(() => {
      page.setData({ searching: true });
      get('/api/inspection-items', { keyword: keyword.trim(), page: 1, page_size: 20 }).then((data) => {
        page.setData({ searchResults: data.list || [], searching: false });
      }).catch(() => {
        page.setData({ searching: false });
      });
    }, 400);
  },

  onPickItem(e) {
    if(this.data.recordId || this.data.createPayload) return;
    const picked = this.data.searchResults.filter((i) => i.id === e.currentTarget.dataset.id)[0];
    if (picked) {
      this.setData({ item: picked, searchResults: [], keyword: '' });
      this.resetAnalyzeState();
    }
  },

  // 换检查项/换照片后重置判别与确认状态（下次判别会创建新记录）
  resetAnalyzeState() {
    this.setData({
      recordId: null,
      aiResult: null,
      aiDisplay: null,
      aiActionable: true,
      mode: '',
    slaHours: null,
    draftReady: false,
      verdict: '',
      note: '',
      draft: { problem_location: '', rectify_requirement: '', basis: '' },
      draftOriginal: null,
      assigneeId: null,
      analyzeFailed: false,
    });
  },

  // ---------- 拍照上传 ----------
  // 现场照片必须现场拍摄（P04 §2.4.1）：仅相机，不提供相册入口；
  // 原图直传不做本地压缩——压缩会剥离 EXIF 拍摄时间/GPS，破坏证据链
  choosePhoto() {
    if(this.data.recordId || this.data.createPayload || this.data.photos.length>=9 || this.data.uploading) return;
    wx.chooseMedia({count:1,mediaType:['image'],sourceType:['camera'],success:async(res)=>{
      try {
        const photo=await drafts.savePhoto(res.tempFiles[0].tempFilePath);
        const photos=this.data.photos.concat(photo);
        this.setData({photos,photoFull:photos[0].path});
        this.syncPhotos().catch(()=>{});
      } catch(e) {wx.showToast({title:e.message,icon:'none'});}
    }});
  },
  retakePhoto() {
    if(this.data.recordId || this.data.createPayload) return;
    this.data.photos.forEach((p)=>{wx.removeSavedFile({filePath:p.path,fail(){}});if(p.id)del('/api/attachments/'+p.id,true).catch(()=>{});});
    this.setData({photos:[],attachmentId:null,photoFull:''});
    this.resetAnalyzeState();
  },
  previewPhoto() {
    if (this.data.photoFull) {
      wx.previewImage({ urls: [this.data.photoFull] });
    }
  },

  // ---------- 一键 AI 判别 ----------
  async analyze() {
    if(!this.data.item || !this.data.photos.length || this.data.analyzing) return;
    this.setData({analyzing:true,analyzeFailed:false});
    try {
      const rec=await this.ensureRecord();
      await post('/api/inspection-records/'+rec.id+'/analysis-job',{});
      this.setData({jobSubmitted:true}); this.pollJob();
    } catch(e) {this.setData({analyzing:false,analyzeFailed:true});}
  },

  buildAiDisplay(ai) {
    ai = ai || {};
    const needReview = ai.has_defect === null || ai.status === 'unknown' || !(typeof ai.confidence === 'number') || ai.confidence < 0.6;
    let verdictText = '初步判断：未见明显异常';
    let verdictClass = 'success';
    if (needReview) {
      verdictText = '建议人工复核';
      verdictClass = 'warn';
    } else if (ai.has_defect) {
      verdictText = '初步判断：疑似异常';
      verdictClass = 'danger';
    }
    return {
      aiDisplay: {
        preliminaryText: ai.preliminary ? (ai.confidence < 0.4 ? '图像信息不足，请补充现场核验' : (ai.preliminary.has_defect ? '初步倾向：疑似异常（待人工确认）' : '初步倾向：未见明显异常（待人工确认）')) : '',
        reviewReason: ai.review_reason || '',
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
      },
    };
  },

  // ---------- 人工确认 ----------
  onVerdictTap(e) {
    const verdict = e.currentTarget.dataset.value;
    if (verdict === this.data.verdict) {
      return;
    }
    this.setData({ verdict: verdict });
    if (verdict === 'abnormal' && !this.data.draftReady) {
      this.loadDraft();
    }
  },

  loadDraft() {
    get('/api/inspection-records/' + this.data.recordId + '/draft').then((draft) => {
      this.setData({ draft: { problem_location: draft.problem_location,
        rectify_requirement: draft.rectify_requirement, basis: draft.basis },
        slaHours: draft.sla_hours, draftReady: true });
      this.loadAssignees();
    }).catch(() => {});
  },

  manualInspect() {
    if (!this.data.item || !this.data.photos.length || this.data.analyzing) return;
    this.setData({ analyzing: true });
    const create = this.ensureRecord();
    create.then((rec) => {
      const ai = { has_defect: null, status: 'unknown', confidence: 0, defect_desc: '请依据现场情况人工判定' };
      this.setData({ recordId: rec.id, analyzing: false, aiResult: ai,
        aiActionable: false, mode: 'manual', aiDisplay: null });
    }).catch(() => this.setData({ analyzing: false }));
  },

  loadAssignees() {
    const page = this;
    get('/api/orders/assignee-options').then((list) => {
      const groups = [];
      const byKey = {};
      (list || []).forEach((u) => {
        const key = u.specialty || 'other';
        if (!byKey[key]) {
          byKey[key] = { key: key, label: SPECIALTY[key] || key, users: [] };
          groups.push(byKey[key]);
        }
        byKey[key].users.push({ user_id: u.user_id, name: u.name, open_count: u.open_count });
      });
      page.setData({ assigneeGroups: groups });
    }).catch(() => {});
  },

  onAssigneeTap(e) {
    this.setData({ assigneeId: e.currentTarget.dataset.id });
  },

  onNoteInput(e) {
    this.setData({ note: e.detail.value });
  },

  onDraftInput(e) {
    this.setData({ ['draft.' + e.currentTarget.dataset.field]: e.detail.value });
  },

  // ---------- 提交确认 ----------
  submit() {
    const d = this.data;
    if (d.submitting) {
      return;
    }
    if (!d.aiResult) {
      wx.showToast({ title: '请先进行 AI 判别或直接人工巡查', icon: 'none' });
      return;
    }
    if (!d.verdict) {
      wx.showToast({ title: '请选择人工确认结论', icon: 'none' });
      return;
    }
    const payload = { human_verdict: d.verdict, note: d.note || null };
    if (d.verdict === 'abnormal') {
      if (!d.assigneeId) {
        wx.showToast({ title: '请选择整改责任人', icon: 'none' });
        return;
      }
      if (!d.draftReady) { this.loadDraft(); return; }
      if (DRAFT_FIELDS.some((f) => !(d.draft[f] || '').trim())) {
        wx.showToast({ title: '请完整填写问题定位、整改要求和依据', icon: 'none' }); return;
      }
      const orderDraft = Object.assign({ assignee_id: d.assigneeId, sla_hours: d.slaHours }, d.draft);
      payload.order_draft = orderDraft;
    }
    const page = this;
    this.setData({ submitting: true });
    // 订阅消息授权（P06 §2.4.2）：提交巡查时为发起人请求后续工单流转通知的授权（静默降级，不阻断提交）
    requestSubscribe(['ORDER_ACCEPTED', 'ORDER_STARTED', 'ORDER_FEEDBACK']);
    if (!this.data.confirmPayload) this.setData({confirmPayload:payload});
    post('/api/inspection-records/' + d.recordId + '/confirm', this.data.confirmPayload,10000,{'Idempotency-Key':this._draftId}).then(() => {
      page._finished=true; drafts.remove(page._draftId);
      wx.showToast({ title: '已提交', icon: 'success' });
      setTimeout(() => {
        page.setData({ submitting: false });
        wx.switchTab({ url: '/pages/inspect/index' });
      }, 800);
    }).catch(() => {
      page.setData({ submitting: false });
    });
  },
});
