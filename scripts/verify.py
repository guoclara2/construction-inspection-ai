"""Reproducible local-and-CI verification entry point for the release candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT_DIR = ROOT / ".verification"
SKIP_PARTS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".test-deps", ".verification", "data", "storage", "uploads", "dist", "deploy", "evaluation"}
SKIP_PARTS.add('.runtime')
SKIP_FILES = {".env", ".env.production"}

def source_hashes(root: Path) -> dict[str, str]:
    """Hash deployable source while excluding secrets and generated files."""
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.relative_to(root).parts):
            continue
        if path.name in SKIP_FILES or path.suffix in {".pyc", ".db"}:
            continue
        result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result

def run(label: str, command: list[str], cwd: Path) -> dict:
    completed = subprocess.run(command, cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True)
    output = (completed.stdout or "") + (completed.stderr or "")
    print(f"[{label}] {' '.join(command)}")
    print(output, end="" if output.endswith("\n") else "\n")
    return {"label": label, "command": command, "returncode": completed.returncode, "output": output}

def main() -> int:
    # Node and pytest output contains CJK and check marks; make redirected Windows consoles deterministic.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--backend-only", action="store_true")
    group.add_argument("--clients-only", action="store_true")
    args = parser.parse_args()
    commands: list[tuple[str, list[str], Path]] = []
    commands.append(("startup-tests", [sys.executable, "-m", "unittest", "discover", "-s", "scripts/tests"], ROOT))
    if not args.clients_only:
        commands.append(("backend-tests", [sys.executable, "-m", "pytest", "app/tests", "-q", "--tb=short", "-p", "no:cacheprovider"], ROOT / "backend"))
    if not args.backend_only:
        mini = ROOT / "miniprogram"
        for script in ("check-mp.js", "check-sandbox-shadow.js", "check-p0.js", "check-p1.js", "check-login.js", "check-release.js"):
            commands.append((f"mini-{script}", ["node", f"scripts/{script}"], mini))
        commands.append(("mini-release-unit", ["node", "--test", "scripts/check-release.test.js"], mini))
        admin = ROOT / "admin-web"
        npm = "npm.cmd" if sys.platform == "win32" else "npm"
        commands.extend((("admin-tests", [npm, "test"], admin), ("admin-build", [npm, "run", "build"], admin)))
    results = [run(label, command, cwd) for label, command, cwd in commands]
    RESULT_DIR.mkdir(exist_ok=True)
    manifest = {"generated_at": datetime.now(timezone.utc).isoformat(), "python": sys.version, "commands": results, "source_hashes": source_hashes(ROOT)}
    (RESULT_DIR / "verification.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [r["label"] for r in results if r["returncode"]]
    if failures:
        print("验证失败：" + ", ".join(failures), file=sys.stderr)
        return 1
    print("验证通过；证据已写入 .verification/verification.json")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
