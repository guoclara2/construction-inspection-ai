// Meaningful client recovery tests without a real WeChat runtime.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const store=new Map([['userInfo',{id:2}],['projectId',1]]);
global.wx={getStorageSync:k=>store.get(k),setStorageSync:(k,v)=>store.set(k,JSON.parse(JSON.stringify(v))),
  removeSavedFile:()=>{},removeStorageSync:k=>store.delete(k),getAccountInfoSync:()=>({miniProgram:{envVersion:'develop'}})};
const drafts=require('../utils/drafts');
const ns=drafts.namespace(),id=drafts.uuid();
drafts.save(id,{photos:[{path:'/private/photo',id:null}],note:'离线备注'},ns);
assert.equal(drafts.list()[0].data.note,'离线备注');
store.set('projectId',2);assert.equal(drafts.list().length,0);
assert.throws(()=>drafts.save(id,{note:'串项目'},ns));
store.set('projectId',1);store.set('userInfo',{id:3});assert.equal(drafts.list().length,0);
store.set('userInfo',{id:2});assert.equal(drafts.list()[0].id,id);
drafts.remove(id);assert.equal(drafts.list().length,0);
let uploadCalls=0,refreshCalls=0;
global.wx.uploadFile=(options)=>{uploadCalls++;options.success({statusCode:uploadCalls===1?401:200,
  data:JSON.stringify(uploadCalls===1?{code:401}:{code:0,data:{id:88}})});};
global.wx.request=(options)=>{refreshCalls++;options.success({statusCode:200,data:{code:0,data:{access_token:'new-test-token'}}});};
global.wx.showToast=()=>{};global.wx.reLaunch=()=>{throw new Error('Must refresh before redirect');};
store.set('refreshToken','test-refresh');
const api=require('../utils/api');
async function run(){
  const att=await api.upload('/private/photo',{biz_type:'record',key:'stable-upload-key',shotAt:'2026-09-08T10:00:00Z',location:null});
  assert.equal(att.id,88);assert.equal(uploadCalls,2);assert.equal(refreshCalls,1);
  let page,failUpload=true,creates=0,getResponse={};
  const fakeApi={get:async()=>getResponse,post:async(url,body)=>{creates++;return {id:99};},del:async()=>{},
    upload:async()=>{if(failUpload)throw new Error('offline');return{id:44,evidence:{reasons:[]}};},signedImageUrl:async()=>''};
  const sandbox={Page:p=>{page=p;},getApp:()=>({fetchUnread(){}}),wx:global.wx,console,setTimeout,clearTimeout,
    require:(name)=>name.endsWith('/api')?fakeApi:name.endsWith('/drafts')?drafts:name.endsWith('/dict')?{SPECIALTY:{},SEVERITY_COLOR:{}}:{requestSubscribe(){}}};
  vm.runInNewContext(fs.readFileSync(path.join(root,'pages/inspect/form.js'),'utf8'),sandbox);
  page.data=JSON.parse(JSON.stringify(page.data));
  page.setData=function(patch){for(const [key,value] of Object.entries(patch)){const fields=key.split('.');if(fields.length===1)this.data[key]=value;else this.data[fields[0]][fields[1]]=value;}};
  page.onLoad({});page.setData({item:{id:1,name:'检查'},photos:[{path:'/private/photo',key:'photo-key-123',shotAt:'2026-09-08T10:00:00Z',location:null}],location:{building:'A'}});
  await assert.rejects(()=>page.syncPhotos());
  assert.equal(drafts.list()[0].data.photos[0].path,'/private/photo');
  failUpload=false;await page.ensureRecord();await page.ensureRecord();
  assert.equal(creates,1);assert.equal(page.data.recordId,99);
  assert.equal(drafts.list()[0].data.createPayload.request_key,page._draftId);
  const preliminary={has_defect:null,status:'unknown',confidence:.82,basis:'',basis_verified:false,
    defect_desc:'右侧疑似缺少防护',preliminary:{has_defect:true},review_reason:'依据待核验'};
  getResponse={status:'done',stage:'分析完成',record:{ai_result:preliminary,ai_mode:'real'}};
  page.setData({analyzing:true,jobSubmitted:true});await page.pollJob();
  assert.equal(page.data.analyzing,false);assert.equal(page.data.aiActionable,false);
  assert.match(page.data.aiDisplay.preliminaryText,/疑似异常/);
  assert.equal(page.data.aiDisplay.defect_desc,'右侧疑似缺少防护');
  assert.equal(page.data.aiDisplay.reviewReason,'依据待核验');
  getResponse={status:'failed',stage:'失败',error:'今日额度用尽'};
  await page.pollJob();assert.equal(page.data.jobStage,'今日额度用尽');
  console.log('AI client PASS: preliminary survives polling, unverified result cannot prefill, task error visible');
  console.log('P1 client PASS: account/project isolation, persisted offline photo, upload auth refresh, stable record request, record reuse');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
