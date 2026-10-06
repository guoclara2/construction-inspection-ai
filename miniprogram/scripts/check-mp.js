#!/usr/bin/env node
// 小程序结构校验：页面四件套 / 禁用未完成字样 / 枚举映射完整性
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
let failed = false;

function fail(msg) {
  console.error('FAIL: ' + msg);
  failed = true;
}

// 1. app.json 每个页面路径四件套（.js/.wxml/.json/.wxss）存在
const appJson = JSON.parse(fs.readFileSync(path.join(ROOT, 'app.json'), 'utf8'));
if (!Array.isArray(appJson.pages) || appJson.pages.length === 0) {
  fail('app.json pages 为空');
}
for (const p of appJson.pages) {
  for (const ext of ['.js', '.wxml', '.json', '.wxss']) {
    const f = path.join(ROOT, p + ext);
    if (!fs.existsSync(f)) {
      fail('页面 ' + p + ' 缺少文件 ' + p + ext);
    }
  }
}

// 2. 全项目 js/wxml 无未完成字样（拼接构造避免脚本自身误报）
const BAD_WORDS = ['TO' + 'DO', 'FIX' + 'ME', '占' + '位'];
function walk(dir, files) {
  for (const name of fs.readdirSync(dir)) {
    const f = path.join(dir, name);
    const stat = fs.statSync(f);
    if (stat.isDirectory()) {
      walk(f, files);
    } else if (/\.(js|wxml)$/.test(name)) {
      files.push(f);
    }
  }
  return files;
}
const allFiles = walk(ROOT, []);
if (allFiles.length === 0) {
  fail('未扫描到任何 js/wxml 文件');
}
for (const f of allFiles) {
  const content = fs.readFileSync(f, 'utf8');
  for (const word of BAD_WORDS) {
    if (content.indexOf(word) !== -1) {
      fail('文件 ' + path.relative(ROOT, f) + ' 含未完成字样: ' + word);
    }
  }
}

// 3. utils/dict.js 覆盖全部枚举映射（总纲第 4/8 章）
const dict = require(path.join(ROOT, 'utils', 'dict.js'));
const REQUIRED = {
  ROLE: ['inspector', 'rectifier', 'admin'],
  SPECIALTY: ['civil', 'mech', 'deco', 'safety'],
  STATUS: ['pending', 'accepted', 'processing', 'review', 'closed'],
  SEVERITY: ['轻微', '中度', '严重', '无'],
  VERDICT: ['normal', 'abnormal'],
  RISK: ['high', 'medium', 'low'],
  CATEGORY: ['structural', 'protection', 'installation', 'material', 'safety'],
  TYPE: ['building', 'road', 'tunnel', 'landscape'],
  PHASE: ['foundation', 'structure', 'mep', 'decoration'],
};
for (const name of Object.keys(REQUIRED)) {
  const map = dict[name];
  if (!map || typeof map !== 'object') {
    fail('dict.' + name + ' 缺失');
    continue;
  }
  for (const key of REQUIRED[name]) {
    if (!map[key]) {
      fail('dict.' + name + ' 缺少键 ' + key);
    }
  }
}

// 4. tabBar：4 项且图标资源齐全；custom tabBar 组件四件套存在（按权限过滤 tab）
const tabBar = appJson.tabBar;
if (!tabBar || !Array.isArray(tabBar.list) || tabBar.list.length !== 4) {
  fail('tabBar 须为 4 项');
} else {
  for (const item of tabBar.list) {
    for (const iconKey of ['iconPath', 'selectedIconPath']) {
      if (item[iconKey] && !fs.existsSync(path.join(ROOT, item[iconKey]))) {
        fail('tabBar 项 ' + item.pagePath + ' 图标缺失: ' + item[iconKey]);
      }
    }
  }
}
if (tabBar && tabBar.custom) {
  for (const ext of ['.js', '.json', '.wxml', '.wxss']) {
    const f = path.join(ROOT, 'custom-tab-bar', 'index' + ext);
    if (!fs.existsSync(f)) {
      fail('custom-tab-bar 缺少文件 custom-tab-bar/index' + ext);
    }
  }
}

// 5. api.js 导入一致性：页面/组件 js 中使用的 api 函数必须在 require 解构里
// （防"运行时 ReferenceError → 页面挂死在加载中"类事故，如 login 页曾漏导入 request）
// 注意：不能 require api.js（模块顶层调用 wx API，Node 环境会崩），改为静态解析 module.exports
const API_EXPORTS = [];
const apiContent = fs.readFileSync(path.join(ROOT, 'utils', 'api.js'), 'utf8');
const exportMatch = apiContent.match(/module\.exports\s*=\s*\{([\s\S]*?)\};/);
if (!exportMatch) {
  fail('utils/api.js 未找到 module.exports 导出块');
} else {
  for (const part of exportMatch[1].split(',')) {
    const seg = part.trim();
    if (!seg) {
      continue;
    }
    const name = (seg.split(':')[0] || '').trim();
    if (name && name !== 'BASE_URL') {
      API_EXPORTS.push(name);
    }
  }
}
const JS_DIRS = ['pages', 'custom-tab-bar'];
for (const dir of JS_DIRS) {
  const dirPath = path.join(ROOT, dir);
  if (!fs.existsSync(dirPath)) {
    continue;
  }
  for (const f of walk(dirPath, [])) {
    const content = fs.readFileSync(f, 'utf8');
    // 提取 require('.../utils/api') 的解构导入名（兼容 upload: uploadFile 重命名）
    const m = content.match(/const\s*\{([^}]*)\}\s*=\s*require\(['"][^'"]*utils\/api['"]\)/);
    const imported = new Set();
    if (m) {
      for (const part of m[1].split(',')) {
        const seg = part.trim();
        if (!seg) {
          continue;
        }
        // "upload: uploadFile" 取别名；"get" 取本名
        const alias = seg.split(':')[1] || seg.split(':')[0];
        imported.add(alias.trim());
      }
    }
    // 文件中以函数调用形式使用的 api 导出名，必须已导入（模块自身 utils/api.js 除外）
    for (const name of API_EXPORTS) {
      const useRe = new RegExp('(?<![\\w.$\'"])' + name + '\\s*\\(');
      if (useRe.test(content) && !imported.has(name)) {
        fail('文件 ' + path.relative(ROOT, f) + ' 调用了 ' + name + '() 但未从 utils/api 导入');
      }
    }
  }
}

if (failed) {
  console.error('校验未通过');
  process.exit(1);
}
console.log('ALL PASS');
