import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createSessionRefresh } from '../src/utils/session-refresh.js';
function fixture(post) {
 const values = new Map([['token', 'old'], ['refreshToken', 'r1']]);
 const storage = { getItem: k => values.get(k) || null };
 const store = { setTokens(a, r) { this.token = a; this.refreshToken = r; values.set('token', a); values.set('refreshToken', r); } };
 return { values, store, run: createSessionRefresh({ post, storage, getStore: () => store }) };
}
test('concurrent refresh shares one bounded request and synchronizes state', async () => {
 let calls = 0, resolve;
 const f = fixture((url, body, config) => { calls++; assert.equal(config.timeout, 10000); assert.equal(body.refresh_token, 'r1'); return new Promise(r => resolve = r); });
 const first = f.run(), second = f.run(); assert.equal(first, second);
 resolve({ data: { code: 0, data: { access_token: 'new', refresh_token: 'r2' } } });
 assert.equal(await first, true); assert.equal(calls, 1); assert.equal(f.store.token, 'new'); assert.equal(f.values.get('refreshToken'), 'r2');
});
test('timeout/failure releases singleflight for retry; malformed success is rejected', async () => {
 let calls = 0;
 const f = fixture(async () => { if (++calls === 1) throw new Error('timeout'); return { data: { code: 0, data: {} } }; });
 assert.equal(await f.run(), false); assert.equal(await f.run(), false); assert.equal(calls, 2); assert.equal(f.values.get('token'), 'old');
});
test('inflight refresh cannot resurrect logout or overwrite another login', async () => {
 for (const newSession of [false, true]) {
 let resolve; const f = fixture(() => new Promise(r => resolve = r)); const pending = f.run();
 f.values.delete('token'); f.values.delete('refreshToken');
 if (newSession) { f.values.set('token', 'other'); f.values.set('refreshToken', 'other-r'); }
 resolve({ data: { code: 0, data: { access_token: 'stale', refresh_token: 'stale-r' } } });
 assert.equal(await pending, newSession); assert.equal(f.values.get('token'), newSession ? 'other' : undefined);
 }
});
