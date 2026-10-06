# 附件证据链用例（P04 完成判定的机制化验证）：
# C1 公开静态目录移除 / C2 magic bytes / C4 EXIF+SHA256+水印 / C5 现拍强约束 /
# C7 上传限流 / 鉴权下载与跨项目隔离 / 可见性 / 软删与证据固化保护
import hashlib
import io
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.database import SessionLocal
from app.models import Attachment, Project
from app.services import ratelimit
from app.services.storage import get_storage

# 种子用户：1=admin(org_admin) 2=李明(P1 inspector) 3=王芳(P1+P2 inspector)
#           4=赵强(P1 rectifier) 7=陈静(P2 inspector) 9=林伟(P1 reviewer)
ADMIN, LIMING, WANGFANG, ZHAO, CHENJING, LINWEI = 1, 2, 3, 4, 7, 9


@pytest.fixture(autouse=True)
def _reset_upload_ratelimit():
    """清零进程内限流窗口：隔离用例间/测试文件间的上传计数（测试恒为进程内实现）"""
    ratelimit._local_buckets.clear()
    yield
    ratelimit._local_buckets.clear()


def _jpeg_with_exif(shot_local: datetime | None = None, *, software: str | None = None,
                    gps: bool = True) -> bytes:
    """构造带完整存证 EXIF 的 JPEG：相机 Make/Model + DateTimeOriginal + GPS（北京坐标）"""
    import piexif
    from PIL import Image

    if shot_local is None:
        shot_local = datetime.now(ZoneInfo("Asia/Shanghai"))
    exif = {
        "0th": {piexif.ImageIFD.Make: b"TestCam", piexif.ImageIFD.Model: b"XC-100"},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: shot_local.strftime("%Y:%m:%d %H:%M:%S").encode()},
    }
    if gps:
        exif["GPS"] = {
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((39, 1), (54, 1), (20, 1)),
            piexif.GPSIFD.GPSLongitudeRef: b"E",
            piexif.GPSIFD.GPSLongitude: ((116, 1), (23, 1), (29, 1)),
        }
    if software:
        exif["0th"][piexif.ImageIFD.Software] = software.encode()
    # 唯一标记：EXIF 拍摄时间只有秒级精度，避免相邻用例生成字节级相同的图片而误命中重复检测
    exif["0th"][piexif.ImageIFD.ImageDescription] = uuid.uuid4().hex.encode()
    img = Image.new("RGB", (640, 480), (110, 140, 90))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=piexif.dump(exif))
    return buf.getvalue()


@pytest.fixture()
def exif_jpeg() -> bytes:
    return _jpeg_with_exif()


def _upload(client, headers, data: bytes, name="shot.jpg", **form):
    form.setdefault("biz_type", "record")
    form.setdefault("source", "camera")
    return client.post("/api/attachments", data=form,
                       files={"file": (name, data, "application/octet-stream")}, headers=headers)


def _upload_ok(client, headers, data: bytes, name="shot.jpg", **form) -> dict:
    r = _upload(client, headers, data, name, **form)
    assert r.json()["code"] == 0, r.text
    return r.json()["data"]


def _item_id(client, headers) -> int:
    r = client.get("/api/inspection-items?keyword=支护", headers=headers)
    return r.json()["data"]["list"][0]["id"]


def _create_record(client, headers, att_id: int) -> int:
    r = client.post("/api/inspection-records",
                    json={"item_id": _item_id(client, headers), "attachment_id": att_id}, headers=headers)
    assert r.json()["code"] == 0, r.text
    return r.json()["data"]["id"]


def _create_order(client, headers, att_id: int, assignee_id: int) -> dict:
    """上传的记录 → analyze → confirm(abnormal+order_draft) → 工单"""
    record_id = _create_record(client, headers, att_id)
    client.post(f"/api/inspection-records/{record_id}/analyze", headers=headers)
    r = client.post(f"/api/inspection-records/{record_id}/confirm",
                    json={"human_verdict": "abnormal", "note": "确认异常",
                          "order_draft": {"assignee_id": assignee_id, "problem_location": "东侧支护裂缝", "rectify_requirement": "加固后现场复核", "basis": "检查项依据"}}, headers=headers)
    assert r.json()["code"] == 0, r.text
    return r.json()["data"]["order"]


# ---------- C1：公开静态目录与免鉴权访问 ----------

def test_uploads_static_mount_gone(client):
    """/uploads 公开静态目录必须已移除（任何路径一律 404）"""
    assert client.get("/uploads/any.jpg").status_code == 404


def test_upload_requires_auth(client, png_bytes):
    r = client.post("/api/attachments", data={"biz_type": "record", "source": "camera"},
                    files={"file": ("a.png", png_bytes, "image/png")})
    assert r.status_code == 401


def test_file_requires_auth(client, auth_headers, exif_jpeg):
    d = _upload_ok(client, auth_headers(LIMING), exif_jpeg)
    assert client.get(f"/api/attachments/{d['id']}/file").status_code == 401


# ---------- C2：magic bytes 与扩展名一致性 ----------

def test_disguised_file_rejected(client, auth_headers):
    """文本内容伪装 .jpg → 真实类型识别失败 → 1042"""
    r = _upload(client, auth_headers(LIMING), b"this is definitely not an image " * 8, name="fake.jpg")
    assert r.json()["code"] == 1042


def test_extension_mismatch_rejected(client, auth_headers, png_bytes):
    """PNG 内容声明 .jpg 扩展名（真实类型与扩展名不一致）→ 1042"""
    r = _upload(client, auth_headers(LIMING), png_bytes, name="fake.jpg")
    assert r.json()["code"] == 1042


def test_bad_biz_type_rejected(client, auth_headers, png_bytes):
    r = _upload(client, auth_headers(LIMING), png_bytes, name="a.png", biz_type="avatar")
    assert r.json()["code"] == 1000


# ---------- C5：整改反馈必须现场拍摄 ----------

def test_order_feedback_album_rejected_at_upload(client, auth_headers, exif_jpeg):
    """上传阶段：biz_type=order_feedback 且 source=album → 1041"""
    r = _upload(client, auth_headers(ZHAO), exif_jpeg, name="fb.jpg",
                biz_type="order_feedback", source="album")
    assert r.json()["code"] == 1041


def test_order_feedback_album_rejected_at_binding(client, auth_headers, exif_jpeg):
    """绑定阶段二次校验（纵深防御）：模拟存量非相机反馈附件 → feedback 时 1041"""
    db = SessionLocal()
    try:
        p1 = db.query(Project).filter_by(code="XCDS").first()
        att = Attachment(
            org_id=p1.org_id, project_id=p1.id, biz_type="order_feedback", biz_id=None,
            file_key="legacy/album.jpg", raw_key="legacy/album.jpg", file_name="album.jpg",
            mime="image/jpeg", size=len(exif_jpeg), media_type="image",
            sha256=hashlib.sha256(exif_jpeg).hexdigest(), source="album",
            watermarked=False, suspicious=True, uploader_id=ZHAO,
        )
        db.add(att)
        db.commit()
        att_id = att.id
    finally:
        db.close()

    order = _create_order(client, auth_headers(LIMING),
                          _upload_ok(client, auth_headers(LIMING), exif_jpeg)["id"], ZHAO)
    client.post(f"/api/orders/{order['id']}/accept", headers=auth_headers(ZHAO))
    client.post(f"/api/orders/{order['id']}/start", headers=auth_headers(ZHAO))
    r = client.post(f"/api/orders/{order['id']}/feedback",
                    json={"attachment_id": att_id, "text": "用历史相册图反馈"}, headers=auth_headers(ZHAO))
    assert r.json()["code"] == 1041


# ---------- C4：EXIF 存证信息 / 反作弊标记 ----------

def test_upload_with_full_evidence(client, auth_headers):
    """EXIF 完整：sha256/exif_time/gps/水印齐备且不存证异常"""
    shot = datetime.now(ZoneInfo("Asia/Shanghai"))
    jpeg = _jpeg_with_exif(shot)
    d = _upload_ok(client, auth_headers(LIMING), jpeg)
    assert d["sha256"] == hashlib.sha256(jpeg).hexdigest()
    assert len(d["sha256"]) == 64
    # EXIF 时间按项目时区往返：返回值应与拍摄墙钟完全一致
    assert d["exif_time"] == shot.strftime("%Y-%m-%d %H:%M:%S")
    assert round(d["gps_lat"], 4) == 39.9056      # 39°54'20"N
    assert round(d["gps_lng"], 4) == 116.3914     # 116°23'29"E
    assert d["watermarked"] is True
    assert d["suspicious"] is False
    assert "suspicious_reasons" not in d
    assert d["url"] == f"/api/attachments/{d['id']}/file"
    assert d["thumb_url"] == f"/api/attachments/{d['id']}/file?thumb=1"


def test_plain_png_flagged_suspicious(client, auth_headers, png_bytes):
    """无 EXIF 的图片：缺拍摄时间/无定位/无相机信息 → suspicious 三项原因"""
    d = _upload_ok(client, auth_headers(LIMING), png_bytes, name="plain.png")
    assert d["suspicious"] is True
    assert "缺少拍摄时间存证" in d["suspicious_reasons"]
    assert "无定位信息" in d["suspicious_reasons"]
    assert "照片疑似经编辑软件生成" in d["suspicious_reasons"]


def test_stale_shot_time_flagged(client, auth_headers):
    """拍摄时间与接收时间相差 3 天（>120 分钟阈值）→ 存证异常"""
    jpeg = _jpeg_with_exif(datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=3))
    d = _upload_ok(client, auth_headers(LIMING), jpeg)
    assert d["suspicious"] is True
    assert d["suspicious_reasons"] == ["拍摄时间与上传时间相差过大"]


def test_edited_software_flagged(client, auth_headers):
    """EXIF Software 命中编辑软件特征 → 存证异常"""
    jpeg = _jpeg_with_exif(software="Adobe Photoshop 25.0")
    d = _upload_ok(client, auth_headers(LIMING), jpeg)
    assert d["suspicious"] is True
    assert d["suspicious_reasons"] == ["照片疑似经编辑软件生成"]


def test_client_gps_fallback(client, auth_headers, png_bytes):
    """EXIF 无 GPS 时采用客户端声明坐标存证"""
    d = _upload_ok(client, auth_headers(LIMING), png_bytes, name="loc.png",
                   client_lat="31.2304", client_lng="121.4737")
    assert round(d["gps_lat"], 4) == 31.2304
    assert round(d["gps_lng"], 4) == 121.4737


def test_duplicate_photo_marked(client, auth_headers):
    """同业务重复哈希 → suspicious=true + hint（不拒绝，交人工判断）"""
    jpeg = _jpeg_with_exif()
    d1 = _upload_ok(client, auth_headers(LIMING), jpeg)
    assert d1["suspicious"] is False
    d2 = _upload_ok(client, auth_headers(LIMING), jpeg)
    assert d2["suspicious"] is True
    assert d2["hint"] == "疑似重复使用的照片"
    assert d2["suspicious_reasons"] == ["疑似重复使用的照片"]
    assert d2["sha256"] == d1["sha256"]


# ---------- 原图保全与水印呈现 ----------

def test_raw_preserved_and_watermark_served(client, auth_headers):
    """原图只写一次不被覆盖：raw_key 对象哈希=上传哈希；下载返回的是水印图"""
    jpeg = _jpeg_with_exif()
    d = _upload_ok(client, auth_headers(LIMING), jpeg)
    db = SessionLocal()
    try:
        att = db.get(Attachment, d["id"])
        assert att.file_key != att.raw_key
        raw = get_storage().get(att.raw_key)
        assert hashlib.sha256(raw).hexdigest() == d["sha256"]  # 原图未被动过
        wm = get_storage().get(att.file_key)
        assert wm != raw and wm[:2] == b"\xff\xd8"             # 水印图为重编码 JPEG
    finally:
        db.close()
    r = client.get(f"/api/attachments/{d['id']}/file", headers=auth_headers(LIMING))
    assert r.status_code == 200
    assert r.headers["cache-control"] == "private, max-age=300"
    assert r.content == wm                                        # 对外呈现水印图


def test_file_requires_auth_no_query_token(client, auth_headers, exif_jpeg):
    """P0-3：无凭证访问 /file → 401；?access_token= 令牌进 URL 的旧方案已移除，同样 401"""
    d = _upload_ok(client, auth_headers(LIMING), exif_jpeg)
    assert client.get(f"/api/attachments/{d['id']}/file").status_code == 401
    token = auth_headers(LIMING)["Authorization"][7:]
    r = client.get(f"/api/attachments/{d['id']}/file?access_token={token}")
    assert r.status_code == 401


def test_signed_url_flow(client, auth_headers, exif_jpeg):
    """P0-3 签名 URL 主链路：Bearer 换取 → 无头访问签名 URL 200；URL 不含 access_token"""
    d = _upload_ok(client, auth_headers(LIMING), exif_jpeg)
    r = client.get(f"/api/attachments/{d['id']}/signed-url", headers=auth_headers(LIMING))
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["expires_in"] == 300
    assert "access_token" not in data["url"]
    assert all(k in data["url"] for k in ("uid=", "exp=", "sig="))
    # <img> 标签场景：无 Authorization 头，仅凭签名访问
    r2 = client.get(data["url"])
    assert r2.status_code == 200
    assert r2.headers["content-type"].startswith("image/")


def test_signed_url_expired_or_tampered(client, auth_headers, exif_jpeg):
    """P0-3：过期签名 / 篡改签名 / 冒用他人签名 → 401"""
    import time as _time

    from app.routers.attachments import _attachment_sig

    d = _upload_ok(client, auth_headers(LIMING), exif_jpeg)
    att_id = d["id"]
    # 过期：签名本身合法但 exp 已过
    exp = int(_time.time()) - 10
    sig = _attachment_sig(att_id, exp, LIMING)
    r = client.get(f"/api/attachments/{att_id}/file?uid={LIMING}&exp={exp}&sig={sig}")
    assert r.status_code == 401
    # 篡改：exp 变造后签名不匹配
    exp2 = int(_time.time()) + 300
    r = client.get(f"/api/attachments/{att_id}/file?uid={LIMING}&exp={exp2}&sig={sig}")
    assert r.status_code == 401
    # 冒用：不存在的用户 + 伪造签名（无 JWT_SECRET 不可能算出合法 sig）→ 401
    fake_sig = _attachment_sig(att_id, exp2, 999)
    r = client.get(f"/api/attachments/{att_id}/file?uid=999&exp={exp2}&sig={fake_sig}")
    assert r.status_code == 401


def test_signed_url_visible_scope_required(client, auth_headers, exif_jpeg):
    """P0-3：无可见性的用户不可换取签名 URL（沿用 meta 403 语义）"""
    d = _upload_ok(client, auth_headers(LIMING), exif_jpeg)   # 未绑定业务：仅上传者可见
    r = client.get(f"/api/attachments/{d['id']}/signed-url", headers=auth_headers(WANGFANG))
    assert r.status_code == 403


def test_thumb_served(client, auth_headers, exif_jpeg):
    """?thumb=1 返回服务端生成的 512px 缩略图"""
    d = _upload_ok(client, auth_headers(LIMING), exif_jpeg)
    full = client.get(f"/api/attachments/{d['id']}/file", headers=auth_headers(LIMING))
    r = client.get(f"/api/attachments/{d['id']}/file?thumb=1", headers=auth_headers(LIMING))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")
    assert len(r.content) <= len(full.content)


# ---------- 项目隔离与业务可见性 ----------

def test_cross_project_access_denied(client, auth_headers, exif_jpeg):
    """P2 用户取 P1 附件：meta/file 均 403；伪造 X-Project-Id 仍 403"""
    d = _upload_ok(client, auth_headers(LIMING), exif_jpeg)   # liming 默认 P1
    other = auth_headers(CHENJING)                             # chenjing 默认 P2
    assert client.get(f"/api/attachments/{d['id']}", headers=other).status_code == 403
    assert client.get(f"/api/attachments/{d['id']}/file", headers=other).status_code == 403
    spoof = {**other, "X-Project-Id": "1"}
    assert client.get(f"/api/attachments/{d['id']}/file", headers=spoof).status_code == 403


def test_unbound_attachment_private_to_uploader(client, auth_headers, exif_jpeg):
    """未绑定业务的附件仅上传者可见（同项目其他成员也 403）"""
    d = _upload_ok(client, auth_headers(LIMING), exif_jpeg)
    assert client.get(f"/api/attachments/{d['id']}", headers=auth_headers(WANGFANG)).status_code == 403
    assert client.get(f"/api/attachments/{d['id']}/file", headers=auth_headers(WANGFANG)).status_code == 403


def test_record_attachment_visibility(client, auth_headers, exif_jpeg):
    """绑定巡查记录后：本人/本项目可读全部角色（reviewer、org_admin）可见，无关成员 403"""
    d = _upload_ok(client, auth_headers(LIMING), exif_jpeg)
    _create_record(client, auth_headers(LIMING), d["id"])
    assert client.get(f"/api/attachments/{d['id']}", headers=auth_headers(LIMING)).status_code == 200
    assert client.get(f"/api/attachments/{d['id']}", headers=auth_headers(LINWEI)).status_code == 200
    assert client.get(f"/api/attachments/{d['id']}", headers=auth_headers(ADMIN)).status_code == 200
    # 无关本项目成员（与该记录无关联的 rectifier）→ 403
    assert client.get(f"/api/attachments/{d['id']}", headers=auth_headers(ZHAO)).status_code == 403


# ---------- 绑定校验 ----------

def test_record_cannot_use_others_or_mismatched_photo(client, auth_headers, exif_jpeg):
    """绑定校验：他人照片 → 403；用途不匹配（order_extra 用于 record）→ 1008"""
    others = _upload_ok(client, auth_headers(WANGFANG), exif_jpeg)
    r = client.post("/api/inspection-records",
                    json={"item_id": _item_id(client, auth_headers(LIMING)), "attachment_id": others["id"]},
                    headers=auth_headers(LIMING))
    # P2-2 契约：权限不足统一 HTTP 403 包裹体
    assert r.status_code == 403 and r.json()["detail"]["code"] == 403

    extra = _upload_ok(client, auth_headers(LIMING), exif_jpeg, name="ex.jpg", biz_type="order_extra")
    r = client.post("/api/inspection-records",
                    json={"item_id": _item_id(client, auth_headers(LIMING)), "attachment_id": extra["id"]},
                    headers=auth_headers(LIMING))
    assert r.json()["code"] == 1008


# ---------- 软删与证据固化（§2.6） ----------

def test_delete_unbound_by_uploader(client, auth_headers, exif_jpeg):
    """未绑定附件：上传者可软删 → 接口 404，但存储对象保留（30 天物理清理窗口）"""
    d = _upload_ok(client, auth_headers(LIMING), exif_jpeg)
    db = SessionLocal()
    try:
        raw_key = db.get(Attachment, d["id"]).raw_key
    finally:
        db.close()
    r = client.delete(f"/api/attachments/{d['id']}", headers=auth_headers(LIMING))
    assert r.json()["code"] == 0
    assert client.get(f"/api/attachments/{d['id']}", headers=auth_headers(LIMING)).status_code == 404
    assert client.delete(f"/api/attachments/{d['id']}", headers=auth_headers(LIMING)).status_code == 404
    assert get_storage().exists(raw_key)                    # 软删不清对象


def test_delete_by_visible_non_uploader_denied(client, auth_headers, exif_jpeg):
    """可见但非上传者（reviewer）→ 403 仅上传者可删除"""
    d = _upload_ok(client, auth_headers(LIMING), exif_jpeg)
    _create_record(client, auth_headers(LIMING), d["id"])
    r = client.delete(f"/api/attachments/{d['id']}", headers=auth_headers(LINWEI))
    assert r.status_code == 403


def test_delete_frozen_record_evidence_denied(client, auth_headers, exif_jpeg):
    """已确认记录的证据固化 → 1043"""
    d = _upload_ok(client, auth_headers(LIMING), exif_jpeg)
    record_id = _create_record(client, auth_headers(LIMING), d["id"])
    client.post(f"/api/inspection-records/{record_id}/analyze", headers=auth_headers(LIMING))
    r = client.post(f"/api/inspection-records/{record_id}/confirm",
                    json={"human_verdict": "normal", "note": "复核正常"}, headers=auth_headers(LIMING))
    assert r.json()["code"] == 0
    r = client.delete(f"/api/attachments/{d['id']}", headers=auth_headers(LIMING))
    assert r.json()["code"] == 1043


def test_delete_frozen_feedback_evidence_denied(client, auth_headers, exif_jpeg):
    """已提交反馈的附件随工单固化 → 1043"""
    order = _create_order(client, auth_headers(LIMING),
                          _upload_ok(client, auth_headers(LIMING), exif_jpeg)["id"], ZHAO)
    client.post(f"/api/orders/{order['id']}/accept", headers=auth_headers(ZHAO))
    client.post(f"/api/orders/{order['id']}/start", headers=auth_headers(ZHAO))
    fb = _upload_ok(client, auth_headers(ZHAO), exif_jpeg, name="fb.jpg", biz_type="order_feedback")
    r = client.post(f"/api/orders/{order['id']}/feedback",
                    json={"attachment_id": fb["id"], "text": "已完成整改"}, headers=auth_headers(ZHAO))
    assert r.json()["code"] == 0
    r = client.delete(f"/api/attachments/{fb['id']}", headers=auth_headers(ZHAO))
    assert r.json()["code"] == 1043


# ---------- C7：上传限流 ----------

def test_upload_rate_limited(client, auth_headers, png_bytes):
    """同用户 60 次/分钟：前 60 次成功，第 61 次 → 429 + Retry-After"""
    headers = auth_headers(CHENJING)
    for i in range(61):
        r = _upload(client, headers, png_bytes, name="rl.png")
        if i < 60:
            assert r.status_code == 200 and r.json()["code"] == 0, (i, r.text)
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == 429
    assert int(r.headers["Retry-After"]) > 0
