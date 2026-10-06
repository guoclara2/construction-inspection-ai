"""并发建单压测（P05 验证门 §四.2，消灭 D3 撞号）。

用法（在 backend 目录下，目标服务须为 dev + DEV_LOGIN_ENABLED=true + AI mock 模式）：
    python app/tests/load/concurrent_create_orders.py --concurrency 100

流程：
  阶段 1（串行）：双巡查员交替造 N 条已 analyze 未确认的记录
                 （上传限流 60 次/分钟/人，2 人分摊支持 N ≤ 120）
  阶段 2（并发）：N 线程同时对各自记录 confirm 建单（各自携带 Idempotency-Key）

预期：N 张工单全部成功、order_no 互不相同、0 个 5xx。
"""
import argparse
import struct
import sys
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

import httpx

# 种子用户：2=李明(inspector) 3=王芳(inspector) 4=赵强(rectifier)，默认项目均为 XCDS
INSPECTORS = [2, 3]
ASSIGNEE = 4


def png_bytes() -> bytes:
    """最小合法 PNG（1x1）"""
    def chunk(ctype, data):
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


def login(client: httpx.Client, user_id: int) -> dict:
    r = client.post("/api/auth/dev-login", json={"user_id": user_id})
    assert r.json()["code"] == 0, f"dev-login user={user_id} 失败：{r.text}"
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def prepare_record(client: httpx.Client, headers: dict, tag: str) -> int:
    """上传 → 建记录 → analyze，返回未确认的 record_id"""
    r = client.post("/api/attachments", data={"biz_type": "record", "source": "camera"},
                    files={"file": (f"{tag}.png", png_bytes(), "image/png")}, headers=headers)
    assert r.json()["code"] == 0, f"上传失败：{r.text}"
    att_id = r.json()["data"]["id"]

    r = client.get("/api/inspection-items?keyword=支护", headers=headers)
    items = r.json()["data"]["list"]
    assert items, "keyword=支护 无匹配检查项（种子未初始化？）"
    r = client.post("/api/inspection-records",
                    json={"item_id": items[0]["id"], "attachment_id": att_id}, headers=headers)
    assert r.json()["code"] == 0, f"建记录失败：{r.text}"
    record_id = r.json()["data"]["id"]

    r = client.post(f"/api/inspection-records/{record_id}/analyze", headers=headers)
    assert r.json()["code"] == 0, f"analyze 失败：{r.text}"
    return record_id


def main() -> int:
    ap = argparse.ArgumentParser(description="P05 并发建单压测")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--concurrency", type=int, default=100)
    args = ap.parse_args()
    n = args.concurrency

    client = httpx.Client(base_url=args.base_url, timeout=60)
    inspector_headers = [login(client, u) for u in INSPECTORS]

    # ---- 阶段 1：串行造记录（双巡查员分摊上传限流）----
    print(f"[阶段1] 串行准备 {n} 条未确认记录（双巡查员交替）...")
    t0 = time.perf_counter()
    tasks = []  # (record_id, headers)
    for i in range(n):
        h = inspector_headers[i % len(inspector_headers)]
        tasks.append((prepare_record(client, h, f"load{i}"), h))
        if (i + 1) % 20 == 0:
            print(f"  已准备 {i + 1}/{n}")
    t_prep = time.perf_counter() - t0
    print(f"[阶段1] 完成，耗时 {t_prep:.1f}s")

    # ---- 阶段 2：并发 confirm 建单 ----
    print(f"[阶段2] {n} 并发确认建单...")
    barrier = threading.Barrier(n)
    results = []  # (status_code, code, order_no)
    lock = threading.Lock()

    def worker(item):
        record_id, h = item
        barrier.wait()
        r = client.post(f"/api/inspection-records/{record_id}/confirm",
                        json={"human_verdict": "abnormal",
                              "order_draft": {"assignee_id": ASSIGNEE, "problem_location": "东侧支护裂缝", "rectify_requirement": "加固后现场复核", "basis": "检查项依据"}},
                        headers={**h, "Idempotency-Key": f"load-{record_id}"})
        body = r.json()
        order_no = (body.get("data") or {}).get("order", {}).get("order_no") if body.get("code") == 0 else None
        with lock:
            results.append((r.status_code, body.get("code"), order_no))

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n) as ex:
        for item in tasks:
            ex.submit(worker, item)
    t_burst = time.perf_counter() - t0

    # ---- 断言与报告 ----
    http_ok = [r for r in results if r[0] < 500]
    biz_ok = [r for r in results if r[1] == 0]
    order_nos = [r[2] for r in biz_ok]
    errors_5xx = [r for r in results if r[0] >= 500]
    dup = len(order_nos) - len(set(order_nos))

    print("\n===== 结果 =====")
    print(f"并发数        : {n}")
    print(f"业务成功      : {len(biz_ok)}/{n}")
    print(f"工单号去重后  : {len(set(order_nos))}（重复 {dup}）")
    print(f"HTTP 5xx      : {len(errors_5xx)}")
    print(f"并发阶段耗时  : {t_burst:.2f}s（{n / t_burst:.0f} 单/秒）")
    if len(biz_ok) != n:
        fails = [r for r in results if r[1] != 0][:5]
        print("失败样例      :", fails)
    if order_nos:
        print(f"工单号样例    : {sorted(order_nos)[:3]} ...")

    ok = len(biz_ok) == n and dup == 0 and not errors_5xx
    print("\n结论:", "PASS — 100% 成功且工单号互不相同" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
