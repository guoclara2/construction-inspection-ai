const {get} = require('../../utils/api');
const drafts = require('../../utils/drafts');
const STATUS = {
  pending: ['待检', 'tag-primary'],
  missed: ['漏检', 'tag-danger'],
  done: ['按时完成', 'tag-success'],
  late: ['补检', 'tag-warn'],
  cancelled: ['已取消', 'tag-info'],
};
Page({
  data:{tasks:[],drafts:[],counts:{},error:''},
  onShow(){
    this.setData({drafts:drafts.list()});
    get('/api/plan-tasks').then((r)=>{
      const list=(r.list||[]).map((t)=>{
        const s=STATUS[t.status] || [t.status,'tag-info'];
        return Object.assign({},t,{statusLabel:s[0],statusClass:s[1]});
      });
      this.setData({tasks:list,counts:r.counts,error:''});
    })
      .catch(()=>this.setData({error:'暂时无法获取任务；本地草稿仍可继续编辑'}));
  },
  resume(e){wx.navigateTo({url:'/pages/inspect/form?draft_id='+e.currentTarget.dataset.id});},
  start(e){
    const t=this.data.tasks.find((x)=>x.id===e.currentTarget.dataset.id);
    if(t.record_id){wx.navigateTo({url:'/pages/record-detail/index?id='+t.record_id});return;}
    const existing=drafts.list().find((d)=>d.data.taskId===t.id);
    wx.navigateTo({url:existing ? '/pages/inspect/form?draft_id='+existing.id : '/pages/inspect/form?item_id='+t.item_id+'&task_id='+t.id});
  },
  discard(e){
    const id=e.currentTarget.dataset.id;
    wx.showModal({title:'删除本地草稿',content:'将删除本地保存的草稿和照片。已经提交到服务器的记录仍然保留。',
      success:(r)=>{if(r.confirm){drafts.remove(id);this.onShow();}}});
  }
});
