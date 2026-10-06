// 工单详情：三段问题卡片 + 原始记录 + 整改反馈 + 全状态操作（接收/开始/反馈/审核/催办）+ 流转时间线
// 注意：不可解构出顶层 const upload——开发者工具沙箱包装函数含同名参数，会导致 SyntaxError（模块无法注册、页面白屏）
const { get, post, upload: uploadFile, signedImageUrl } = require('../../utils/api');
const { formatTime, evidenceMeta, evidenceIssues } = require('../../utils/format');
const { STATUS, STATUS_COLOR, CATEGORY, VERDICT, VERDICT_COLOR, SPECIALTY } = require('../../utils/dict');
const { requestSubscribe } = require('../../utils/subscribe');

// 日志动作中文（总纲 4.6 枚举）
const localDrafts = require('../../utils/drafts');
const ACTION_CN = {
  create: '创建工单',
  accept: '接收',
  start: '开始整改',
  feedback: '提交反馈',
  review_pass: '审核通过',
  review_reject: '审核打回',
  urge: '催办',
  transfer: '转派',
  cancel: '作废',
};

Page({
  data: {
    rounds: [], extensions: [], extensionDate: '', extensionReason: '',
    id: null,
    loading: true,
    order: null,
    // 头部展示字段
    statusLabel: '',
    statusColor: '',
    roundLabel: '',
    overdueText: '',
    deadlineText: '',
    createdText: '',
    // 三段
    problem_location: '',
    rectify_requirement: '',
    basis: '',
    // 原始记录区
    record: null,
    photoFull: '',
    photoMeta: null,          // 记录照片存证信息（拍摄时间/坐标/来源/存证异常）
    aiLabel: '',
    aiVerdictColor: '',
    aiDesc: '',
    // 整改反馈区
    feedbackPhotoFull: '',
    feedbackMeta: null,       // 反馈照片存证信息
    feedbackText: '',
    feedbackArchivedHint: '',  // 打回后清空 → 归档提示
    // 角色与按钮可见性
    isAssignee: false,
    isInspector: false,       // 发起巡查员（审核权）
    isAdmin: false,
    showAccept: false,
    showStart: false,
    showFeedback: false,
    showReview: false,
    showUrge: false,
    showTransfer: false,      // 转派（order:transfer：发起巡查员/项目管理员/组织管理员）
    showCancel: false,        // 作废（order:cancel：同上，未闭环未作废可操作）
    acting: false,            // 操作防重复
    // 转派弹层
    transferOpen: false,
    transferLoading: false,
    transferOptions: [],      // [{user_id,name,label,open_count}]
    transferNames: [],        // picker 显示名
    transferIndex: -1,
    transferReason: '',
    transferSubmitting: false,
    // 作废弹层
    cancelOpen: false,
    cancelReason: '',
    cancelSubmitting: false,
    // 整改反馈弹层
    feedbackOpen: false,
    fbPhotos: [],
    fbAttachmentId: null,     // 反馈照片附件 id（先传后绑）
    fbPhotoFull: '',
    fbUploading: false,
    fbText: '',
    fbSubmitting: false,
    // 打回意见弹窗
    rejectOpen: false,
    rejectComment: '',
    rejectSubmitting: false,
    // 时间线（倒序）
    logs: [],
  },

  onExtensionDate(e){this.setData({extensionDate:e.detail.value});},
  onExtensionReason(e){this.setData({extensionReason:e.detail.value});},
  async applyExtension(){
    if(!this.data.extensionDate || !this.data.extensionReason.trim()) {wx.showToast({title:'请填写日期和原因',icon:'none'});return;}
    await post('/api/orders/'+this.data.id+'/extensions',{deadline:this.data.extensionDate+'T23:59:59+08:00',reason:this.data.extensionReason,expected_version:this.data.order.version});
    this.loadOrder();
  },
  reviewExtension(e){
    wx.showModal({title:'延期审批意见',editable:true,placeholderText:'请输入理由',success:async(r)=>{
      if(!r.confirm || !r.content.trim())return;
      await post('/api/orders/'+this.data.id+'/extensions/'+e.currentTarget.dataset.id+'/review',{
        approve:e.currentTarget.dataset.approve==='yes',comment:r.content,expected_version:this.data.order.version});this.loadOrder();
    }});
  },
  previewRound(e){const r=this.data.rounds.find((r)=>r.round===e.currentTarget.dataset.round);wx.previewImage({urls:r.photos.map((p)=>p.signedUrl)});},
  onLoad(options) {
    this.setData({ id: options && options.id });
  },

  onShow() {
    getApp().fetchUnread();
    this.loadOrder();
  },

  loadOrder() {
    const page = this;
    if (!this.data.id) {
      return;
    }
    get('/api/orders/' + this.data.id).then((data) => {
      page.setData(page.buildDisplay(data));
      page._fbKey=localDrafts.namespace()+':feedback:'+data.id;
      const saved=wx.getStorageSync(page._fbKey);
      if(saved){
        const ids=(data.rounds||[]).flatMap((r)=>(r.photos||[]).map((p)=>p.id));
        if((saved.photos||[]).some((p)=>p.id && ids.indexOf(p.id)!==-1))page.clearFbDraft(saved.photos);
        else page.setData({fbPhotos:saved.photos||[],fbText:saved.text||'',fbAttachmentId:(saved.photos[0]||{}).id||null,fbPhotoFull:(saved.photos[0]||{}).path||''});
      }
      page.loadPhotoUrls(data);
      get('/api/orders/'+data.id+'/extensions').then((extensions)=>page.setData({extensions})).catch(()=>{});
      Promise.all((data.rounds||[]).map(async(r)=>Object.assign({},r,{photos:await Promise.all((r.photos||[]).map(async(p)=>Object.assign({},p,{signedUrl:await signedImageUrl(p.id)})))})))
        .then((rounds)=>page.setData({rounds})).catch(()=>{});
      wx.stopPullDownRefresh();
    }).catch(() => {
      page.setData({ loading: false });
      wx.stopPullDownRefresh();
    });
  },

  // 照片短时效签名 URL（P0-3）：经 /signed-url 换取后展示，令牌不进 URL
  loadPhotoUrls(data) {
    const page = this;
    const record = data.record || {};
    if (record.photo) {
      signedImageUrl(record.photo.id).then((url) => {
        page.setData({ photoFull: url });
      }).catch(() => {});
    }
    const feedbackPhoto = (data.feedback_photos || [])[0] || null;
    if (feedbackPhoto) {
      signedImageUrl(feedbackPhoto.id).then((url) => {
        page.setData({ feedbackPhotoFull: url });
      }).catch(() => {});
    }
  },

  buildDisplay(data) {
    const userInfo = wx.getStorageSync('userInfo') || {};
    const uid = userInfo.id;
    const roles = (userInfo.roles || []).slice();
    if (userInfo.is_org_admin && roles.indexOf('org_admin') === -1) {
      roles.push('org_admin');
    }
    const status = data.status;
    const isAssignee = uid === data.assignee_id;
    const isInspector = uid === data.inspector_id;
    // 催办权限对齐后端 order:urge（project_admin / org_admin）
    const isAdmin = roles.indexOf('project_admin') !== -1 || roles.indexOf('org_admin') !== -1;
    // 转派/作废权限对齐后端 order:transfer / order:cancel（inspector / project_admin / org_admin）
    const canTransfer = roles.indexOf('inspector') !== -1
      || roles.indexOf('project_admin') !== -1 || roles.indexOf('org_admin') !== -1;
    const TRANSFERABLE = ['pending', 'accepted', 'processing'];
    // 当前轮次反馈附件（打回的历史轮次归档保留，以最新 round 为准）
    const feedbackPhotos = data.feedback_photos || [];
    const feedbackPhoto = feedbackPhotos[0] || null;
    // 打回后未重新提交：整改中且第 2 轮起且本轮反馈为空
    const feedbackArchivedHint = (status === 'processing' && data.round > 1
      && !feedbackPhoto && !data.feedback_text)
      ? '上一轮反馈已归档至时间线，请重新整改' : '';
    const record = data.record || {};
    const ai = record.ai_result || {};
    const logs = (data.logs || []).slice().reverse().map((lg) => ({
      id: lg.id,
      actionLabel: ACTION_CN[lg.action] || lg.action,
      operator_name: lg.operator_name,
      remark: lg.remark,
      time: formatTime(lg.created_at),
    }));
    return {
      loading: false,
      order: data,
      statusLabel: STATUS[status] || status,
      statusColor: STATUS_COLOR[status] || '#5B6B7D',
      roundLabel: data.round > 1 ? '第 ' + data.round + ' 轮' : '',
      overdueText: data.overdue && data.overdue_hours ? '已超期 ' + data.overdue_hours + ' 小时' : (data.overdue ? '已超期' : ''),
      deadlineText: formatTime(data.deadline),
      createdText: formatTime(data.created_at),
      problem_location: data.problem_location,
      rectify_requirement: data.rectify_requirement,
      basis: data.basis,
      record: {
        id: record.id,
        item_name: record.item_name,
        defectCategoryLabel: CATEGORY[record.defect_category] || record.defect_category,
        humanVerdictLabel: record.human_verdict ? ('人工确认：' + (VERDICT[record.human_verdict] || record.human_verdict)) : '',
        humanVerdictClass: record.human_verdict === 'abnormal' ? 'tag-danger' : 'tag-success',
      },
      photoFull: '',
      photoMeta: evidenceMeta(record.photo),
      photoIssues: (record.photo && record.photo.suspicious) ? evidenceIssues(record.photo) : [],
      aiLabel: ai.has_defect === null || ai.confidence < 0.6 ? 'AI：无法判断，请人工复核' : ai.has_defect === true ? 'AI 判别：存在异常' : (ai.has_defect === false ? 'AI 判别：未发现异常' : ''),
      aiVerdictColor: ai.has_defect ? VERDICT_COLOR.abnormal : VERDICT_COLOR.normal,
      aiDesc: ai.defect_desc || '',
      feedbackPhotoFull: '',
      feedbackMeta: evidenceMeta(feedbackPhoto),
      feedbackIssues: (feedbackPhoto && feedbackPhoto.suspicious) ? evidenceIssues(feedbackPhoto) : [],
      feedbackText: data.feedback_text || '',
      feedbackArchivedHint: feedbackArchivedHint,
      isAssignee: isAssignee,
      isInspector: isInspector,
      isAdmin: isAdmin,
      showAccept: isAssignee && status === 'pending',
      showStart: isAssignee && status === 'accepted',
      showFeedback: isAssignee && status === 'processing',
      showReview: (isInspector || roles.indexOf('reviewer') !== -1) && !isAssignee && status === 'review',
      showUrge: isAdmin && status !== 'closed' && status !== 'cancelled',
      showTransfer: canTransfer && TRANSFERABLE.indexOf(status) !== -1,
      showCancel: canTransfer && status !== 'closed' && status !== 'cancelled',
      acting: false,
      logs: logs,
    };
  },

  // ---------- 通用动作（接收 / 开始整改 / 催办） ----------
  doAction(e) {
    const action = e.currentTarget.dataset.action;
    if (this.data.acting) {
      return;
    }
    // 接收工单时请求订阅授权（P06 §2.4.2）：责任人将收到催办/临期/超期/审核结果通知（静默降级）
    if (action === 'accept') {
      requestSubscribe(['ORDER_REVIEW_PASS', 'ORDER_REVIEW_REJECT', 'ORDER_URGE', 'ORDER_WARN', 'ORDER_OVERDUE']);
    }
    const page = this;
    this.setData({ acting: true });
    // 乐观锁（P05 §2.6）：携带当前版本回传；他人已更新 → 后端 1014，提示后刷新重试
    post('/api/orders/' + this.data.id + '/' + action, {
      expected_version: this.data.order && this.data.order.version,
    }).then(() => {
      wx.showToast({ title: '操作成功', icon: 'success' });
      page.loadOrder();
    }).catch(() => {
      // 业务错误 msg 已由请求层 toast（含 1014 版本冲突/催办 429 限流提示）；刷新取最新版本
      page.setData({ acting: false });
      page.loadOrder();
    });
  },

  // ---------- 转派（order:transfer：发起巡查员/项目管理员/组织管理员） ----------
  openTransfer() {
    const page = this;
    this.setData({ transferOpen: true, transferIndex: -1, transferReason: '', transferSubmitting: false });
    if (this.data.transferOptions.length) {
      return; // 已加载过候选人列表（同项目内复用）
    }
    this.setData({ transferLoading: true });
    get('/api/orders/assignee-options').then((list) => {
      // 排除当前责任人；附带未闭环单数辅助选人
      const options = (list || [])
        .filter((o) => o.user_id !== (page.data.order && page.data.order.assignee_id))
        .map((o) => ({
          user_id: o.user_id,
          name: o.name,
          label: o.name + (o.specialty ? '（' + (SPECIALTY[o.specialty] || o.specialty) + '）' : '')
            + '｜未闭环 ' + o.open_count + ' 单',
        }));
      page.setData({
        transferLoading: false,
        transferOptions: options,
        transferNames: options.map((o) => o.label),
      });
    }).catch(() => {
      page.setData({ transferLoading: false });
    });
  },

  closeTransfer() {
    if (this.data.transferSubmitting) {
      return;
    }
    this.setData({ transferOpen: false });
  },

  onTransferPick(e) {
    this.setData({ transferIndex: Number(e.detail.value) });
  },

  onTransferReasonInput(e) {
    this.setData({ transferReason: e.detail.value });
  },

  submitTransfer() {
    const d = this.data;
    if (d.transferSubmitting) {
      return;
    }
    if (d.transferIndex < 0 || !d.transferOptions[d.transferIndex]) {
      wx.showToast({ title: '请选择新责任人', icon: 'none' });
      return;
    }
    const page = this;
    const target = d.transferOptions[d.transferIndex];
    wx.showModal({
      title: '确认转派',
      content: '将工单转派给「' + target.name + '」？转派后由新责任人继续整改。',
      success(res) {
        if (!res.confirm) {
          return;
        }
        page.setData({ transferSubmitting: true });
        post('/api/orders/' + d.id + '/transfer', {
          assignee_id: target.user_id,
          reason: (d.transferReason || '').trim(),
          expected_version: d.order && d.order.version,  // 乐观锁版本回传（P05 §2.6）
        }).then(() => {
          wx.showToast({ title: '已转派给 ' + target.name, icon: 'none' });
          page.setData({ transferOpen: false, transferSubmitting: false, transferOptions: [], transferNames: [] });
          page.loadOrder();
        }).catch(() => {
          // 1014 版本冲突/权限等提示由请求层 toast；刷新详情取最新版本
          page.setData({ transferSubmitting: false });
          page.loadOrder();
        });
      },
    });
  },

  // ---------- 作废（order:cancel：误建工单等退出路径，原因必填） ----------
  openCancel() {
    this.setData({ cancelOpen: true, cancelReason: '', cancelSubmitting: false });
  },

  closeCancel() {
    if (this.data.cancelSubmitting) {
      return;
    }
    this.setData({ cancelOpen: false });
  },

  onCancelReasonInput(e) {
    this.setData({ cancelReason: e.detail.value });
  },

  submitCancel() {
    const d = this.data;
    if (d.cancelSubmitting) {
      return;
    }
    if (!(d.cancelReason || '').trim()) {
      wx.showToast({ title: '请填写作废原因', icon: 'none' });
      return;
    }
    const page = this;
    this.setData({ cancelSubmitting: true });
    post('/api/orders/' + d.id + '/cancel', {
      reason: d.cancelReason.trim(),
      expected_version: d.order && d.order.version,  // 乐观锁版本回传（P05 §2.6）
    }).then(() => {
      wx.showToast({ title: '工单已作废', icon: 'success' });
      page.setData({ cancelOpen: false, cancelSubmitting: false });
      page.loadOrder();
    }).catch(() => {
      page.setData({ cancelSubmitting: false });
      page.loadOrder();
    });
  },

  // ---------- 整改反馈弹层 ----------
  // 阻止弹层内容区 tap 冒泡到遮罩层的空方法（catchtap="" 空字符串绑定在运行时无效，
  // 会导致点弹层内任意位置时冒泡触发 closeFeedback、弹层瞬间关闭）
  noop() {},

  openFeedback() {
    this.setData({ feedbackOpen: true });
  },

  closeFeedback() {
    if (this.data.fbSubmitting) {
      return;
    }
    this.setData({ feedbackOpen: false });
  },

  // 整改反馈照片必须现场拍摄（P04 §2.4.1）：仅相机；原图直传保留 EXIF 存证
  saveFbDraft(){
    if(this._fbKey)wx.setStorageSync(this._fbKey,{photos:this.data.fbPhotos,text:this.data.fbText});
  },
  clearFbDraft(photos){
    (photos||this.data.fbPhotos).forEach((p)=>wx.removeSavedFile({filePath:p.path,fail(){}}));
    if(this._fbKey)wx.removeStorageSync(this._fbKey);
    this.setData({fbPhotos:[],fbText:'',fbAttachmentId:null,fbPhotoFull:'',feedbackOpen:false});
  },
  chooseFbPhoto(){
    if(this.data.fbPhotos.length>=9 || this.data.fbUploading || this.data.fbSubmitting)return;
    wx.chooseMedia({count:1,mediaType:['image'],sourceType:['camera'],success:async(res)=>{
      try{
        const photo=await localDrafts.savePhoto(res.tempFiles[0].tempFilePath);
        const photos=this.data.fbPhotos.concat(photo);
        this.setData({fbPhotos:photos,fbPhotoFull:photos[0].path});this.saveFbDraft();
        this.syncFeedbackPhotos().catch(()=>{});
      }catch(e){wx.showToast({title:e.message || '本地照片保存失败',icon:'none'});}
    }});
  },
  async syncFeedbackPhotos(){
    if(this._fbKey!==localDrafts.namespace()+':feedback:'+this.data.id)throw new Error('账号或项目已切换');
    this.setData({fbUploading:true});
    try{
      const photos=this.data.fbPhotos.slice();
      for(let i=0;i<photos.length;i++)if(!photos[i].id){
        const att=await uploadFile(photos[i].path,{biz_type:'order_feedback',source:'camera',key:photos[i].key,shotAt:photos[i].shotAt,location:photos[i].location});
        photos[i]=Object.assign({},photos[i],{id:att.id});this.setData({fbPhotos:photos.slice(),fbAttachmentId:photos[0].id});this.saveFbDraft();
      }
    }finally{this.setData({fbUploading:false});}
  },
  onFbTextInput(e){this.setData({fbText:e.detail.value});this.saveFbDraft();},
  async submitFeedback(){
    if(this.data.fbSubmitting)return;
    if(!this.data.fbPhotos.length || !this.data.fbText.trim()){wx.showToast({title:'请拍照并填写整改说明',icon:'none'});return;}
    this.setData({fbSubmitting:true});
    try{
      await this.syncFeedbackPhotos();
      await post('/api/orders/'+this.data.id+'/feedback',{attachment_id:this.data.fbPhotos[0].id,
        attachment_ids:this.data.fbPhotos.map((p)=>p.id),text:this.data.fbText.trim(),expected_version:this.data.order.version});
      this.clearFbDraft();wx.showToast({title:'反馈已提交',icon:'success'});
    }catch(e){this.saveFbDraft();}
    finally{this.setData({fbSubmitting:false});this.loadOrder();}
  },

  // ---------- 审核区 ----------
  reviewPass() {
    if (this.data.acting) {
      return;
    }
    const page = this;
    wx.showModal({
      title: '确认闭环',
      content: '确认整改合格并闭环该工单？',
      success(res) {
        if (!res.confirm) {
          return;
        }
        page.setData({ acting: true });
        post('/api/orders/' + page.data.id + '/review', {
          result: 'pass',
          expected_version: page.data.order && page.data.order.version,  // 乐观锁版本回传（P05 §2.6）
        }).then(() => {
          wx.showToast({ title: '已闭环', icon: 'success' });
          page.loadOrder();
        }).catch(() => {
          page.setData({ acting: false });
          page.loadOrder();
        });
      },
    });
  },

  openReject() {
    this.setData({ rejectOpen: true, rejectComment: '' });
  },

  closeReject() {
    if (this.data.rejectSubmitting) {
      return;
    }
    this.setData({ rejectOpen: false });
  },

  onRejectInput(e) {
    this.setData({ rejectComment: e.detail.value });
  },

  submitReject() {
    const d = this.data;
    if (d.rejectSubmitting) {
      return;
    }
    if (!d.rejectComment.trim()) {
      wx.showToast({ title: '请填写打回意见', icon: 'none' });
      return;
    }
    const page = this;
    this.setData({ rejectSubmitting: true });
    post('/api/orders/' + d.id + '/review', {
      result: 'reject',
      comment: d.rejectComment.trim(),
      expected_version: d.order && d.order.version,  // 乐观锁版本回传（P05 §2.6）
    }).then(() => {
      wx.showToast({ title: '已打回', icon: 'success' });
      page.setData({ rejectOpen: false, rejectSubmitting: false });
      page.loadOrder();
    }).catch(() => {
      // 1014 版本冲突等提示由请求层 toast；刷新详情取最新版本，弹层保留已填意见
      page.setData({ rejectSubmitting: false });
      page.loadOrder();
    });
  },

  // ---------- 图片预览 ----------
  previewPhoto() {
    if (this.data.photoFull) {
      wx.previewImage({ urls: [this.data.photoFull] });
    }
  },

  previewFeedbackPhoto() {
    if (this.data.feedbackPhotoFull) {
      wx.previewImage({ urls: [this.data.feedbackPhotoFull] });
    }
  },

  previewFbPhoto() {
    if (this.data.fbPhotoFull) {
      wx.previewImage({ urls: [this.data.fbPhotoFull] });
    }
  },

  goRecord() {
    if (this.data.order && this.data.order.record_id) {
      wx.navigateTo({ url: '/pages/record-detail/index?id=' + this.data.order.record_id });
    }
  },

  onPullDownRefresh() {
    this.loadOrder();
  },
});
