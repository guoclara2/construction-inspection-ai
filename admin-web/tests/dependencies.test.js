import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

test('Element Plus can resolve its Vue shared runtime after npm ci', () => {
  const require = createRequire(import.meta.url);
  const fromElementPlus = createRequire(require.resolve('element-plus'));
  assert.ok(fromElementPlus.resolve('@vue/shared'));
  assert.equal(typeof fromElementPlus('@vue/shared').isArray, 'function');
});
