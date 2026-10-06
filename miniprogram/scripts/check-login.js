const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert/strict');
let loginCalls=0,home=0,navigation=[],calls=[],page;
const values={};
const auth={access_token:'test',refresh_token:'test-r',user:{id:10,project_id:10},projects:[{id:10,name:'独立体验'}],must_change_password:false};
const wx={getStorageSync:k=>values[k],setStorageSync:(k,v)=>values[k]=v,
  login:({success})=>{loginCalls++;success({code:'wx-code'});},showLoading(){},hideLoading(){},showToast(){},
  navigateTo:({url})=>navigation.push(url),reLaunch:({url})=>navigation.push(url)};
const api={postRaw:async(url,body)=>{calls.push({url,body});return{code:0,data:auth};}};
function load(name){
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../pages',name,'index.js'),'utf8'),{
    Page:p=>page=p,wx,getApp:()=>({globalData:{},landHome:()=>home++}),
    setInterval,clearInterval,require:n=>n.endsWith('/api')?api:n.endsWith('/env')?{IS_DEV:false}:{ROLE:{},SPECIALTY:{}}
  });
  page.setData=p=>Object.assign(page.data,p);
}
(async()=>{
  load('login');page.onWxLogin();await new Promise(r=>setImmediate(r));
  assert.equal(home,1);assert.equal(navigation.length,0);assert.equal(calls[0].url,'/api/auth/wx-login');assert.equal(calls[0].body.code,'wx-code');
  load('wx-bind');page.onLoad({});assert.equal(loginCalls,1); // phone page did not call wx.login
  page.data.phone='13900000000';page.data.smsCode='123456';page.submit();await new Promise(r=>setImmediate(r));
  assert.equal(calls.at(-1).url,'/api/auth/phone-login');assert.equal(calls.at(-1).body.bind_ticket,undefined);
  assert.equal(home,2);
  // Execute the real transport: both cloud and HTTPS must carry the verified login code.
  for (const useCloud of [true,false]) {
    let outgoing;
    const response={statusCode:200,data:{code:0,data:auth}};
    const transportWx={...wx,cloud:{callContainer:async options=>{outgoing=options;return response;}},request:options=>{outgoing=options;options.success(response);}};
    const context={module:{exports:{}},wx:transportWx,require:()=>({BASE_URL:'https://inspection.cn',USE_CLOUD:useCloud,CLOUD_RUN:{env:'prod-real',service:'inspect'}})};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../utils/api.js'),'utf8'),context);
    await context.module.exports.postRaw('/api/auth/wx-login',{code:'verified-wx-code'});
    assert.equal(outgoing.data.code,'verified-wx-code');
    assert.equal(useCloud?outgoing.path:outgoing.url,useCloud?'/api/auth/wx-login':'https://inspection.cn/api/auth/wx-login');
  }
  console.log('PASS: WeChat lands directly; phone login has no WeChat or binding dependency');
})().catch(e=>{console.error(e);process.exitCode=1;});
