const { test } = require('node:test');
const assert = require('node:assert/strict');
const { validateRelease } = require('./check-release');
const project = { appid: 'wx1234567890abcdef', setting: { urlCheck: true } };
const cloud = { releaseMode: 'cloudrun', trial: '', release: '', cloudRun: { env: 'prod-real123', service: 'inspect' } };
const https = { releaseMode: 'https', trial: 'https://trial.inspection.cn', release: 'https://inspection.cn' };
test('valid cloud and HTTPS deployments', () => {
  assert.equal(validateRelease(cloud, project), 'cloudrun');
  assert.equal(validateRelease(https, project), 'https');
});
test('reject incomplete, ambiguous and insecure release configuration', () => {
  for (const cfg of [ { ...cloud, releaseMode: undefined }, { ...cloud, releaseMode: 'auto' },
    { ...cloud, cloudRun: { env: 'prod-real123' } }, { ...cloud, cloudRun: { env: 'example-env', service: 'inspect' } },
    ...['', 'http://inspection.cn', 'https://localhost', 'https://192.168.1.1', 'https://[::1]', 'https://example.com', 'https://user:pass@inspection.cn', 'https://inspection.cn/?token=x'].map(release => ({ ...https, release })) ]) {
    assert.throws(() => validateRelease(cfg, project));
  }
  assert.throws(() => validateRelease(cloud, { ...project, appid: 'touristappid' }));
  assert.throws(() => validateRelease(cloud, { ...project, setting: { urlCheck: false } }));
});

test('runtime selects the explicit mode even when unused cloud settings remain', () => {
  const fs = require('node:fs'), vm = require('node:vm');
  const source = fs.readFileSync(require.resolve('../config/env'), 'utf8');
  for (const [cfg, expected] of [[cloud, true], [{ ...https, cloudRun: cloud.cloudRun }, false]]) {
    const context = { module: { exports: {} }, require: () => cfg, wx: { getAccountInfoSync: () => ({ miniProgram: { envVersion: 'release' } }) } };
    vm.runInNewContext(source, context);
    assert.equal(context.module.exports.USE_CLOUD, expected);
  }
});
