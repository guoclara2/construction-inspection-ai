"""Start the local inspection services with isolated ports and readiness checks."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
# 注意：18080 会被 Docker Desktop(com.docker.backend) 占用，本地接口端口用 18090 规避端口冲突。
# 改端口时请同步修改 stop-local.ps1 / admin-web/vite.config.js / miniprogram/config/endpoints.js / login 提示文案。
API_PORT, WEB_PORT = 18090, 15173

def free(port):
    with socket.socket() as sock:
        return sock.connect_ex(('127.0.0.1', port)) != 0

def read(url):
    with urlopen(url, timeout=2) as response:
        return json.load(response)

def wait(url, process, validate):
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError('服务启动失败，请查看 .runtime 中的日志')
        try:
            if validate(read(url)):
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    raise RuntimeError('服务未在 30 秒内就绪，请查看 .runtime 中的日志')

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    os.environ['PYTHONUTF8'] = '1'
    backend = ROOT / 'backend'
    python = backend / '.venv/Scripts/python.exe'
    if not python.exists():
        raise RuntimeError('请先按 README 安装后端虚拟环境及依赖')
    if not (ROOT / 'admin-web/node_modules/vite/bin/vite.js').exists():
        raise RuntimeError('管理端依赖缺失或安装中断，请双击 scripts/setup.bat 修复')
    if not free(API_PORT) or not free(WEB_PORT):
        def _ours(port):
            try:
                return read(f'http://127.0.0.1:{port}/api/health/live').get('data', {}).get('service') == 'site-inspection'
            except (OSError, ValueError):
                return False
        api_up, web_up = _ours(API_PORT), _ours(WEB_PORT)
        if api_up and web_up:
            print(f'本系统已经运行，请访问 http://127.0.0.1:{WEB_PORT}')
            return
        problems = []
        if not api_up and not free(API_PORT):
            problems.append(f'接口端口 {API_PORT} 被占用（不是本系统进程，可能是 Docker 等程序）')
        if not web_up and not free(WEB_PORT):
            problems.append(f'管理端端口 {WEB_PORT} 被占用（不是本系统进程）')
        if not problems:
            problems.append('服务未完整就绪')
        raise RuntimeError('；'.join(problems) + '。若为本系统残留进程请先运行 scripts/stop-all.bat；若是其他程序（如 Docker）占用则需释放端口或修改 scripts/start-local.py 中的端口后重试。')
    (backend / 'data').mkdir(exist_ok=True)
    subprocess.run([str(python), '-m', 'alembic', 'upgrade', 'head'], cwd=backend, check=True)
    logs = ROOT / '.runtime'
    logs.mkdir(exist_ok=True)
    children = []
    try:
        for name, command, cwd, url, validate in (
            ('backend', [str(python), '-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', str(API_PORT)], backend,
             f'http://127.0.0.1:{API_PORT}/openapi.json', lambda data: data.get('info', {}).get('title') == '工程现场智能巡查助手'),
            ('admin', ['node', str(ROOT / 'admin-web/node_modules/vite/bin/vite.js'), '--configLoader', 'runner'], ROOT / 'admin-web',
             f'http://127.0.0.1:{WEB_PORT}/api/health/live', lambda data: data.get('data', {}).get('service') == 'site-inspection'),
        ):
            with (logs / (name + '.log')).open('w', encoding='utf-8') as log:
                process = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            children.append(process)
            wait(url, process, validate)
    except Exception:
        for process in children:
            process.terminate()
        raise
    (logs / 'services.json').write_text(json.dumps({'pids': [p.pid for p in children]}))
    print(f'服务已就绪。管理端：http://127.0.0.1:{WEB_PORT}；接口：http://127.0.0.1:{API_PORT}/docs')

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
