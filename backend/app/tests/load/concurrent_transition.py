"""并发流转压测（P05 验证门 §四.6，消灭 D10 并发状态错乱）。

用法（在 backend 目录下，目标服务须为 dev + DEV_LOGIN_ENABLED=true + AI mock 模式）：
    python app/tests/load/concurrent_transition.py --order-id 1 --concurrency 20
    python app/tests/load/concurrent_transition.py --concurrency 20   # 不指定则现场造一张新单

预期：同一 pending 工单 N 并发 accept → 恰 1 次成功，
     其余返回 400（当前状态不允许该操作）或 1014（版本冲突），无 5xx，最终状态/版本无错乱。
"""
import argparse
import struct
import sys
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

import httpx

INSPECTOR = 2   # 李明
ASSIGNEE = 4    # 赵强


def png_bytes() -> bytes:
    def chunk(ctype, data):
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


def login(client: httpx.Client, user_id: int) -> dict:
    r = client.post("/api/auth/dev-login", json={"user_id": user_id})
    assert r.json()["code"] == 0, f"dev-login user={user_id} 失败：{r.text}"
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def create_pending_order(client: httpx.Client, insp: dict) -> int:
    """现场造一张 pending 工单，返回 order_id"""
    r = client.post("/api/attachments", data={"biz_type": "record", "source": "camera"},
                    files={"file": ("trans.png", png_bytes(), "image/png")}, headers=insp)
    assert r.json()["code"] == 0, f"上传失败：{r.text}"
    att_id = r.json()["data"]["id"]
    r = client.get("/api/inspection-items?keyword=支护", headers=insp)
    r = client.post("/api/inspection-records",
                    json={"item_id": r.json()["data"]["list"][0]["id"], "attachment_id": att_id},
                    headers=insp)
    assert r.json()["code"] == 0, r.text
    record_id = r.json()["data"]["id"]
    r = client.post(f"/api/inspection-records/{record_id}/analyze", headers=insp)
    assert r.json()["code"] == 0, r.text
    r = client.post(f"/api/inspection-records/{record_id}/confirm",
                    json={"human_verdict": "abnormal", "order_draft": {"assignee_id": ASSIGNEE, "problem_location": "东侧支护裂缝", "rectify_requirement": "加固后现场复核", "basis": "检查项依据"}},
                    headers=insp)
    assert r.json()["code"] == 0, r.text
    return r.json()["data"]["order"]["id"]


def main() -> int:
    ap = argparse.ArgumentParser(description="P05 并发流转压测")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--order-id", type=int, default=None, help="待测工单（须为 pending；缺省则现场造一张）")
    args = ap.parse_args()
    n = args.concurrency

    client = httpx.Client(base_url=args.base_url, timeout=60)
    insp = login(client, INSPECTOR)
    zhao = login(client, ASSIGNEE)

    order_id = args.order_id
    if order_id is None:
        print("[准备] 现场造一张 pending 工单...")
        order_id = create_pending_order(client, insp)
    r = client.get(f"/api/orders/{order_id}", headers=insp)
    detail = r.json()["data"]
    print(f"[准备] 工单 {detail['order_no']} 状态={detail['status']} version={detail['version']}")
    if detail["status"] != "pending":
        print("FAIL：目标工单不是 pending 状态")
        return 1

    barrier = threading.Barrier(n)
    results = []  # (status_code, code)
    lock = threading.Lock()

    def worker():
        barrier.wait()
        r = client.post(f"/api/orders/{order_id}/accept",
                        json={"expected_version": 0}, headers=zhao)
        with lock:
            results.append((r.status_code, r.json().get("code")))

    print(f"[压测] {n} 并发 accept ...")
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n) as ex:
        for _ in range(n):
            ex.submit(worker)
    elapsed = time.perf_counter() - t0

    r = client.get(f"/api/orders/{order_id}", headers=insp)
    final = r.json()["data"]

    success = [x for x in results if x[1] == 0]
    rejected = [x for x in results if x[1] in (400, 1014)]
    errors_5xx = [x for x in results if x[0] >= 500]

    print("\n===== 结果 =====")
    print(f"并发数      : {n}")
    print(f"成功        : {len(success)}")
    print(f"被拒(400/1014): {len(rejected)}（400={sum(1 for x in results if x[1] == 400)}, 1014={sum(1 for x in results if x[1] == 1014)})")
    print(f"HTTP 5xx    : {len(errors_5xx)}")
    print(f"耗时        : {elapsed:.2f}s")
    print(f"最终状态    : {final['status']}（version={final['version']}）")

    ok = (len(success) == 1 and len(rejected) == n - 1 and not errors_5xx
          and final["status"] == "accepted" and final["version"] == 1)
    print("\n结论:", "PASS — 恰 1 次成功，无 5xx，状态/版本无错乱" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
