// 用开发者工具真实的模块沙箱包装器（94 个参数名）对全部小程序 JS 做语法检查
// 复现 devtools appservice 的模块解析：const/let/class 与包装器参数同名 → SyntaxError
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const PARAMS = 'require,module,exports,window,top,document,frames,self,location,navigator,localStorage,history,Caches,screen,alert,confirm,prompt,fetch,XMLHttpRequest,WebSocket,webkit,WeixinJSCore,Reporter,print,URL,DOMParser,requestAnimationFrame,getComputedStyle,Node,upload,preview,build,showDecryptedInfo,cleanAppCache,syncMessage,checkProxy,showSystemInfo,showMyOpenId,restoreLocalData,openVendor,openMiniapp,openMiniappBuilder,openMiniappIpa,openCrashDir,openCache,openEngine,cleanEngineWASM,openEditorCache,openToolsLog,showRequestInfo,help,showDebugInfoTable,closeDebug,showDebugInfo,__global,getMessageTunnelInfo,loadBabelMod,openInspect,openGameEngineDebugMode,closeGameEngineDebugMode,openGameEngineAssetsInspect,openUserDataPath,WeixinJSBridge,__WeixinJSBridge,__passWAServiceGlobal__';

function walk(dir, out) {
  fs.readdirSync(dir).forEach((name) => {
    if (name === 'node_modules' || name === 'scripts') return;
    const p = path.join(dir, name);
    const st = fs.statSync(p);
    if (st.isDirectory()) walk(p, out);
    else if (name.endsWith('.js')) out.push(p);
  });
  return out;
}

const files = walk(ROOT, []);
let fail = 0;
files.forEach((f) => {
  const rel = path.relative(ROOT, f);
  const src = fs.readFileSync(f, 'utf8');
  const wrapped = `(${PARAMS}) => {\n${src}\n}`;
  const tmp = path.join(require('os').tmpdir(), 'wrap_check_' + path.basename(f));
  fs.writeFileSync(tmp, wrapped);
  const { execFileSync } = require('child_process');
  try {
    execFileSync('node', ['--check', tmp], { stdio: 'pipe' });
    console.log('PASS  ' + rel);
  } catch (e) {
    fail++;
    const msg = e.stderr ? e.stderr.toString() : e.message;
    console.log('FAIL  ' + rel);
    msg.split('\n').slice(0, 4).forEach((l) => l && console.log('      ' + l));
  }
});
console.log(fail === 0 ? '\nALL PASS' : `\n${fail} file(s) FAILED`);
process.exit(fail === 0 ? 0 : 1);
