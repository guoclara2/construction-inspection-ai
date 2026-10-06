import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

test('attachment IDs and file paths both request a signed URL', async () => {
  const calls=[];
  const source=fs.readFileSync(new URL('../src/utils/request.js',import.meta.url),'utf8');
  const context={request:{get:async path=>{calls.push(path);return {url:'/signed-image'};}}};
  vm.runInNewContext(source.slice(source.indexOf('export function signedImageUrl')).replace('export function','function'),context);
  assert.equal(await context.signedImageUrl(6),'/signed-image');
  assert.equal(await context.signedImageUrl('/api/attachments/7/file'),'/signed-image');
  assert.deepEqual(calls,['/api/attachments/6/signed-url','/api/attachments/7/signed-url']);
});

function operations(post,confirm=async()=>{}) {
  const source=fs.readFileSync(new URL('../src/views/operations/index.vue',import.meta.url),'utf8').split('<script setup>')[1].split('</script>')[0].replace(/^import .*;$/gm,'');
  const context={ref:value=>({value}),onMounted(){},request:{post},ElMessage:{warning(){},success(){}},ElMessageBox:{confirm}};
  vm.runInNewContext(source+'\nthis.formRef=form; this.savingRef=saving; this.errorRef=error;',context);
  return context;
}
test('empty plan is rejected before sending a request',async()=>{
  let calls=0;const c=operations(async()=>{calls++;});
  await c.create();assert.equal(calls,0);assert.equal(c.savingRef.value,false);
});
test('failed plan request and cancelled confirmation do not leak rejections',async()=>{
  const c=operations(async()=>{throw new Error('test failure');},async()=>{throw 'cancel';});
  Object.assign(c.formRef.value,{name:'test',item_id:1,inspector_id:2,start_date:'2026-09-14',end_date:'2026-09-15'});
  await c.create();assert.equal(c.errorRef.value,'test failure');assert.equal(c.savingRef.value,false);
  await c.disable({id:1});
});
