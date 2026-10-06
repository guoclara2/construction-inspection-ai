const fs = require('fs');
const vm = require('vm');
const path = require('path');
const assert = require('assert');
const root = path.resolve(__dirname, '..');
const envCode = fs.readFileSync(path.join(root, 'config/env.js'), 'utf8');
function environment(version, endpoints) {
  const sandbox = { module: { exports: {} }, require: () => endpoints,
    wx: { getAccountInfoSync: () => ({ miniProgram: {envVersion: version} }) } };
  vm.runInNewContext(envCode, sandbox);
  return sandbox.module.exports;
}
assert.throws(() => environment('release', {releaseMode:'https',release:''}));
assert.throws(() => environment('trial', {releaseMode:'https',trial:'http://192.168.1.5'}));
assert.equal(environment('develop', {develop:'http://127.0.0.1:8000'}).BASE_URL, 'http://127.0.0.1:8000');
assert.equal(environment('release', {releaseMode:'https',release:'https://inspection.company.cn'}).IS_DEV, false);
let page, submitted;
vm.runInNewContext(fs.readFileSync(path.join(root, 'pages/inspect/form.js'), 'utf8'), {
  Page: (p) => {page=p;},
  require: (name) => name.endsWith('/api') ? {post:(url, data) => {submitted=data; return new Promise(()=>{});}} :
    name.endsWith('/subscribe') ? {requestSubscribe:()=>{}} : {},
  wx: {showToast:()=>{}},
});
page.setData = function(values) {Object.assign(this.data, values);};
Object.assign(page.data, {aiResult:{has_defect:null},verdict:'abnormal',assigneeId:4,recordId:1,draftReady:true,slaHours:8,
  draft:{problem_location:' 人工确认位置 ',rectify_requirement:'人工确认措施',basis:'人工确认依据'}});
page.submit();
assert.equal(submitted.order_draft.problem_location, ' 人工确认位置 ');
assert.equal(submitted.order_draft.rectify_requirement,'人工确认措施');
assert.equal(submitted.order_draft.sla_hours,8);
console.log('P0 client regression checks passed');
