import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
test('editing sends captured version; concurrent conflict preserves the draft', async () => {
 const source = fs.readFileSync(new URL('../src/views/knowledge/index.vue', import.meta.url), 'utf8').split('<script setup>')[1].split('</script>')[0].replace(/^import .*;$/gm, '');
 let sent, reject;
 const context = { reactive: x => x, ref: value => ({ value }), onMounted() {}, TYPE: {}, PHASE: {}, CATEGORY: {}, RISK: {}, ElMessage: { success() {}, error() {} }, request: { put(url, body) { sent = body; return new Promise((_, r) => reject = r); } } };
 vm.createContext(context); vm.runInContext(source + '\nthis.editor = { dialog, items, itemFormRef, openEditDialog, saveItem };', context);
 const e = context.editor;
 const row = { id: 1, version: 7, enabled: false, name: 'original', check_points: ['point'], basis: 'basis', applicable_types: [], applicable_phases: [] };
 e.items.value = [row]; e.openEditDialog(row); row.version = 8;
 e.dialog.form.name = 'my draft'; e.itemFormRef.value = { validate: fn => fn(true) }; e.saveItem();
 assert.equal(sent.expected_version, 7); assert.equal(sent.enabled, false);
 reject(Object.assign(new Error('version conflict'), { code: 1014 }));
 await new Promise(r => setImmediate(r));
 assert.equal(e.dialog.visible, true); assert.equal(e.dialog.form.name, 'my draft'); assert.equal(e.dialog.saving, false); assert.match(e.dialog.conflict, /保留/);
});
