import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { createSessionRefresh } from '../src/utils/session-refresh.js';
test('real request interceptor exposes knowledge conflict and clears store on expired session', async () => {
 const values = new Map([['token', 'expired'], ['projects', '[]'], ['projectId', '2']]);
 const localStorage = { getItem: k => values.get(k) || null, setItem: (k,v) => values.set(k,v), removeItem: k => values.delete(k) };
 let store;
 const storeSource = fs.readFileSync(new URL('../src/stores/user.js', import.meta.url), 'utf8').replace(/^import .*;$/gm,'').replace('export const useUserStore', 'const useUserStore');
 vm.runInNewContext(storeSource, { localStorage, defineStore: (_, spec) => { store = spec.state(); for (const [name, fn] of Object.entries(spec.actions)) store[name] = fn.bind(store); return () => store; } });
 let success, failure, redirect;
 const request = { interceptors: { request: { use() {} }, response: { use(ok, err) { success=ok; failure=err; } } } };
 const source = fs.readFileSync(new URL('../src/utils/request.js', import.meta.url), 'utf8').replace(/^import .*;$/gm,'').replace('export default request;', '').replace('export function', 'function');
 vm.runInNewContext(source, { axios: { create: () => request }, localStorage, useUserStore: () => store, createSessionRefresh, ElMessage: { error() {} }, router: { replace: path => redirect = path }, window: { location: { pathname: '/knowledge' } } });
 await assert.rejects(success({ data: { code: 1014, msg: '版本冲突' }, config: {} }), error => error.code === 1014);
 await assert.rejects(failure({ response: { status: 401 }, config: { headers: { Authorization: 'Bearer expired' } } }));
 assert.equal(store.token, ''); assert.equal(store.projectId, ''); assert.equal(values.has('projects'), false); assert.equal(redirect, '/login');
});
