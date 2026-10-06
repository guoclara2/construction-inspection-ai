# 附件路由：照片证据链核心（P04 §2.2–2.6）——鉴权上传（magic bytes/EXIF/SHA256/水印/限流）、
# 元数据查询、鉴权流式下载（local）/短期签名 URL（s3）、软删与固化保护。
# 附件一律不可公开直连：本地存储对象在 web 根之外，S3 对象不公开读。
# P0-3：<img>/previewImage 场景改走短时效签名 URL（/signed-url 换取），?access_token= 回退已移除，
# 令牌不再进入 URL（服务器日志/浏览器历史/Referer 均不可见）。
# TODO(P0-3 方案B)：中期可改为独立静态域名 + 一次性 cookie，彻底解耦 API 鉴权与附件呈现。
import hashlib
import hmac
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import ProjectScope, get_membership, project_scope
from ..models import Attachment, InspectionRecord, Project, RectifyOrder, User
from ..schemas import CloudUploadIn
from ..services.audit import write_audit
from ..services.image_meta import (
    detect_real_type,
    extract_exif,
    image_dimensions,
    is_edited_software,
    make_watermark_jpeg,
    mime_consistent,
    sha256_file,
    validate_mime,
)
from ..services.ratelimit import hit
from ..services.storage import get_storage, get_wxcloud_storage
from ..utils import BizError, as_utc, forbidden, now_utc, ok, to_local

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/attachments", tags=["attachments"])

# 上传流式落盘块大小
_CHUNK = 1024 * 256
# 允许的业务类型与来源
BIZ_TYPES = {"record", "order_feedback", "order_extra"}
SOURCES = {"camera", "album", "unknown"}


def attachment_to_dict(att: Attachment) -> dict:
    """附件序列化：url 为鉴权相对路径（前端经 /signed-url 换取短时效链接后访问，P0-3）"""
    return {
        "id": att.id,
        "url": f"/api/attachments/{att.id}/file",
        "thumb_url": f"/api/attachments/{att.id}/file?thumb=1",
        "biz_type": att.biz_type,
        "biz_id": att.biz_id,
        "round": att.round,
        "file_name": att.file_name,
        "mime": att.mime,
        "size": att.size,
        "media_type": att.media_type,
        "sha256": att.sha256,
        "evidence": att.evidence or {"legacy": True},
        "gps_accuracy": float(att.gps_accuracy) if att.gps_accuracy is not None else None,
        "exif_time": _fmt(att.exif_time),
        "gps_lat": float(att.gps_lat) if att.gps_lat is not None else None,
        "gps_lng": float(att.gps_lng) if att.gps_lng is not None else None,
        "source": att.source,
        "watermarked": att.watermarked,
        "suspicious": att.suspicious,
        "created_at": _fmt(att.created_at),
    }


def _fmt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return to_local(as_utc(dt)).strftime("%Y-%m-%d %H:%M:%S")


# ---------- 业务可见性 ----------

def attachment_visible(att: Attachment, scope: ProjectScope, db: Session) -> bool:
    """附件可见性（P04 §2.2）：未绑定→仅上传者；record→本人或可读全部角色；工单附件→工单可见者"""
    if att.biz_id is None:
        return att.uploader_id == scope.user.id
    if att.biz_type == "record":
        rec = db.get(InspectionRecord, att.biz_id)
        if rec is None or rec.project_id != scope.project_id:
            return False
        if rec.inspector_id == scope.user.id or scope.can_read_all:
            return True
        return db.query(RectifyOrder.id).filter(
            RectifyOrder.record_id == rec.id,
            RectifyOrder.project_id == scope.project_id,
            __import__("sqlalchemy").or_(RectifyOrder.assignee_id == scope.user.id, RectifyOrder.inspector_id == scope.user.id),
        ).first() is not None
    if att.biz_type in ("order_feedback", "order_extra"):
        order = db.get(RectifyOrder, att.biz_id)
        if order is None or order.project_id != scope.project_id:
            return False
        return scope.user.id in (order.assignee_id, order.inspector_id) or scope.can_read_all
    return False


def _get_scoped_attachment(attachment_id: int, db: Session, scope: ProjectScope) -> Attachment:
    """取附件并校验：不存在/已软删→404；跨项目→403（写审计）；不可见→403（写审计）"""
    att = db.get(Attachment, attachment_id)
    if att is None or att.deleted_at is not None:
        raise HTTPException(status_code=404, detail={"code": 404, "msg": "附件不存在", "data": None})
    if att.project_id != scope.project_id:
        write_audit(db, "attachment_access_denied", actor_id=scope.user.id, target_type="attachment",
                    target_id=att.id, result="denied", org_id=scope.org_id, project_id=scope.project_id)
        raise HTTPException(status_code=403, detail={"code": 403, "msg": "无权访问该附件", "data": None})
    if not attachment_visible(att, scope, db):
        write_audit(db, "attachment_access_denied", actor_id=scope.user.id, target_type="attachment",
                    target_id=att.id, result="denied", org_id=scope.org_id, project_id=scope.project_id)
        raise HTTPException(status_code=403, detail={"code": 403, "msg": "无权访问该附件", "data": None})
    return att


# ---------- 绑定校验（供 inspection / orders 调用） ----------

def load_bindable_attachment(
    db: Session, attachment_id: int, scope: ProjectScope, biz_type: str
) -> Attachment:
    """加载可绑定附件：必须存在、未软删、属本项目、biz_type 匹配、上传者为本人、且尚未绑定其他业务。
    biz_type='order_feedback' 时强制 source=camera（1041，P04 §2.4.2 绑定阶段校验）。"""
    att = db.get(Attachment, attachment_id)
    if att is None or att.deleted_at is not None:
        raise BizError(1008, "照片不存在或已被删除，请重新拍摄上传")
    if att.project_id != scope.project_id:
        raise forbidden("无权使用该照片")
    if att.biz_type != biz_type:
        raise BizError(1008, "照片用途不匹配，请重新上传")
    if att.uploader_id != scope.user.id:
        raise forbidden("仅可使用本人上传的照片")
    if att.biz_id is not None:
        # 绑定后不可改（总纲 §4.5）
        raise BizError(1008, "该照片已绑定其他业务，不可重复使用")
    if biz_type == "order_feedback" and att.source != "camera":
        # 整改反馈必须现场拍摄（消灭 C5）
        raise BizError(1041, "整改反馈照片必须现场拍摄")
    return att


def bind_attachment(att: Attachment, biz_id: int, round_no: int | None = None) -> None:
    """把附件绑定到业务对象（先传后绑；绑定后 biz_id 不可再改）"""
    from sqlalchemy import update
    from sqlalchemy.orm import object_session
    db = object_session(att)
    with db.no_autoflush:
        values = {"biz_id": biz_id}
        if round_no is not None: values["round"] = round_no
        if db.execute(update(Attachment).where(Attachment.id == att.id,
            Attachment.biz_id.is_(None), Attachment.deleted_at.is_(None)).values(**values)).rowcount != 1:
            raise BizError(1014, "附件已被其他请求绑定，请刷新")
    db.expire(att)


# ---------- 上传 ----------

def _parse_shot_at(raw: str | None):
    """解析客户端声明的拍摄时间（ISO 8601；无时区按项目时区），失败返回 None"""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        from zoneinfo import ZoneInfo
        try:
            dt = dt.replace(tzinfo=ZoneInfo(settings.APP_TIMEZONE))
        except Exception:  # noqa: BLE001
            return None
    return dt.astimezone(timezone.utc)


@router.post("")
async def upload_attachment(
    request: Request,
    file: UploadFile,
    biz_type: str = Form(...),
    source: str = Form("unknown"),
    client_lat: float | None = Form(None),
    client_lng: float | None = Form(None),
    shot_at: str | None = Form(None),
    accuracy: float | None = Form(None),
    coordinate_system: str = Form("unknown"),
    upload_key: str | None = Form(None),
    biz_id: int | None = Form(None),
    db: Session = Depends(get_db),
    scope: ProjectScope = Depends(project_scope),
):
    """证据附件上传（P04 §2.3，处理顺序不可调整）：
    流式落盘 → magic bytes → SHA256/重复检测 → EXIF/偏差/反作弊 → 存原图 → 水印图 → 落库。"""
    # 0. 限流：同用户 60 次/分钟（消灭 C7）
    allowed, retry = hit(f"upload:{scope.user.id}", 60, 60)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"code": 429, "msg": "上传过于频繁，请稍后再试", "data": None},
            headers={"Retry-After": str(retry)},
        )

    if biz_id is not None:
        raise BizError(1000, "请先上传未绑定附件，再通过业务提交绑定")
    _validate_upload_params(biz_type, source, client_lat, client_lng, accuracy, coordinate_system)

    # 1a. MIME 白名单校验（第一重，P04 上传口径）：客户端声明 MIME 必须命中白名单
    declared_mime = (file.content_type or "").strip()
    if not validate_mime(declared_mime):
        raise BizError(1042, "文件类型不合法")

    # 1. 流式落盘到临时文件：边写边累计大小，超限立即中断并删除（消灭 C3 一次性 read）
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    fd, tmp_path = tempfile.mkstemp(prefix="att_", suffix=".part")
    os.close(fd)
    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = await file.read(_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                if out.tell() > max_bytes:
                    raise BizError(1006, f"文件大小不能超过 {settings.MAX_UPLOAD_MB}MB")

        return ok(_persist_attachment(
            tmp_path, (file.filename or "")[:200],
            declared_mime=declared_mime,
            biz_type=biz_type, source=source, shot_at=shot_at,
            client_lat=client_lat, client_lng=client_lng, accuracy=accuracy,
            coordinate_system=coordinate_system, upload_key=upload_key,
            db=db, scope=scope,
        ))
    finally:
        # 清理临时文件（成功与失败路径均清理）
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


@router.post("/from-cloud")
def upload_attachment_from_cloud(
    payload: CloudUploadIn,
    db: Session = Depends(get_db),
    scope: ProjectScope = Depends(project_scope),
):
    """云托管对象存储上传通道（微信云托管部署形态，小程序经 callContainer JSON 调用）：
    小程序先 wx.cloud.uploadFile 直传对象存储获得 fileID，再带业务字段调用本接口；
    后端按 fileID 流式下载校验（magic bytes/SHA256/EXIF 存证）→ 水印 → 落库。
    原图不搬运：raw_key 即小程序上传时的对象键（云存储通道）。"""
    allowed, retry = hit(f"upload:{scope.user.id}", 60, 60)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"code": 429, "msg": "上传过于频繁，请稍后再试", "data": None},
            headers={"Retry-After": str(retry)},
        )
    _validate_upload_params(payload.biz_type, payload.source, payload.client_lat,
                             payload.client_lng, payload.accuracy, payload.coordinate_system)

    wxc = get_wxcloud_storage()
    if wxc is None:
        raise BizError(1000, "该部署未启用云托管对象存储上传通道")
    try:
        raw_key = wxc.key_of(payload.file_id)
    except ValueError as exc:
        raise BizError(1000, str(exc))

    fd, tmp_path = tempfile.mkstemp(prefix="att_", suffix=".part")
    os.close(fd)
    try:
        try:
            wxc.download_to_file(payload.file_id, tmp_path,
                                 settings.MAX_UPLOAD_MB * 1024 * 1024)
        except ValueError as exc:
            raise BizError(1006, str(exc))
        except (RuntimeError, OSError) as exc:
            logger.warning("云存储附件下载失败 file_id=%s: %s", payload.file_id, exc)
            raise BizError(1008, "云存储文件读取失败，请重新上传")

        return ok(_persist_attachment(
            tmp_path, (payload.file_name or "")[:200],
            biz_type=payload.biz_type, source=payload.source, shot_at=payload.shot_at,
            client_lat=payload.client_lat, client_lng=payload.client_lng,
            accuracy=payload.accuracy,
            coordinate_system=payload.coordinate_system, upload_key=payload.upload_key,
            db=db, scope=scope, raw_in_cloud=True, cloud_raw_key=raw_key,
        ))
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def _validate_upload_params(biz_type: str, source: str, client_lat: float | None,
                            client_lng: float | None, accuracy: float | None,
                            coordinate_system: str) -> None:
    """上传通道共用入参校验（multipart 与 from-cloud 一致）"""
    if (client_lat is None) != (client_lng is None) or (client_lat is not None and not (-90 <= client_lat <= 90 and -180 <= client_lng <= 180)):
        raise BizError(1000, "定位坐标不合法")
    if accuracy is not None and not 0 <= accuracy <= 100000:
        raise BizError(1000, "定位精度不合法")
    if coordinate_system not in {"wgs84", "gcj02", "unknown"}:
        raise BizError(1000, "坐标系不合法")
    if biz_type not in BIZ_TYPES:
        raise BizError(1000, "biz_type 不合法")
    if source not in SOURCES:
        raise BizError(1000, "source 不合法")
    # 整改反馈照片必须现场拍摄：上传阶段即拒绝（1041），绑定阶段二次校验
    if biz_type == "order_feedback" and source != "camera":
        raise BizError(1041, "整改反馈照片必须现场拍摄")


def _persist_attachment(tmp_path: str, file_name: str, *, declared_mime: str | None = None,
                        biz_type: str, source: str,
                        shot_at: str | None, client_lat: float | None, client_lng: float | None,
                        accuracy: float | None, coordinate_system: str, upload_key: str | None,
                        db: Session, scope: ProjectScope,
                        raw_in_cloud: bool = False, cloud_raw_key: str | None = None) -> dict:
    """已落盘临时文件的统一处理（处理顺序不可调整，P04 §2.3）：
    magic bytes → SHA256/重复检测 → EXIF/偏差/反作弊 → 存原图 → 水印图 → 落库。
    raw_in_cloud=True（云托管通道）时原图已在对象存储，raw_key 直接采用小程序上传时的对象键。"""
    # 2. magic bytes 校验：真实类型白名单 + 与扩展名一致（消灭 C2 伪装）
    detected = detect_real_type(tmp_path, file_name)
    if detected is None:
        raise BizError(1042, "文件类型不合法")
    real_ext, mime, media_type = detected

    # 2b. MIME 双重验证（第二重交叉验证）：客户端声明 MIME 须与 magic bytes 检测 MIME 一致。
    # 云托管通道（from-cloud）无客户端 MIME 声明，跳过本层、仅靠 magic bytes 兜底。
    if not mime_consistent(declared_mime, mime):
        raise BizError(1042, "文件类型不合法")

    # 3. SHA256 + 同业务重复检测（疑似重复不拒绝，交人工判断）
    digest = sha256_file(tmp_path)
    if upload_key:
        if not 8 <= len(upload_key) <= 64: raise BizError(1000, "上传标识长度不合法")
        previous = db.query(Attachment).filter_by(project_id=scope.project_id,uploader_id=scope.user.id,
            upload_key=upload_key).first()
        if previous:
            if previous.sha256 != digest or previous.biz_type != biz_type or previous.deleted_at:
                raise BizError(1014, "上传标识已被使用，请重新拍摄")
            return attachment_to_dict(previous)
    dup_query = db.query(Attachment).filter(
        Attachment.project_id == scope.project_id,
        Attachment.biz_type == biz_type,
        Attachment.sha256 == digest,
        Attachment.deleted_at.is_(None),
    )
    duplicate = dup_query.first() is not None

    # 4. EXIF 提取与存证异常判定
    exif = extract_exif(tmp_path, media_type)
    suspicious = duplicate
    reasons: list[str] = []
    if duplicate:
        reasons.append("疑似重复使用的照片")
    if media_type == "image":
        shot_ref = exif.shot_time or _parse_shot_at(shot_at)
        if shot_ref is None:
            suspicious = True
            reasons.append("缺少拍摄时间存证")
        elif abs(now_utc() - shot_ref) > timedelta(minutes=settings.EXIF_MAX_SKEW_MINUTES):
            suspicious = True
            reasons.append("拍摄时间与上传时间相差过大")
        if exif.gps_lat is None and exif.gps_lng is None and client_lat is None and client_lng is None:
            suspicious = True
            reasons.append("无定位信息")
        if is_edited_software(exif, media_type):
            suspicious = True
            reasons.append("照片疑似经编辑软件生成")

    # 定位：EXIF 优先，缺失时采用客户端声明坐标
    gps_lat = exif.gps_lat
    gps_lng = exif.gps_lng
    if gps_lat is None and gps_lng is None and client_lat is not None and client_lng is not None:
        gps_lat, gps_lng = client_lat, client_lng

    # 5. 存原图（raw_key：只写一次、永不覆盖、不对外直出）
    now = now_utc()
    local = to_local(now)
    base_dir = f"{scope.org_id}/{scope.project_id}/{local.strftime('%Y')}/{local.strftime('%m')}"
    uid = uuid.uuid4().hex
    storage = get_storage()
    if raw_in_cloud:
        # 云托管通道：原图已由小程序直传对象存储，对象键即上传时的 cloudPath，不再搬运
        raw_key = cloud_raw_key or ""
        file_key = raw_key
    else:
        raw_key = f"{base_dir}/{uid}_raw.{real_ext}"
        file_key = raw_key
        storage.put_file(raw_key, tmp_path, mime)

    # 6. 生成水印图（对外呈现版本；失败或关闭时回退原图，绝不反向覆盖原图）
    watermarked = False
    if settings.WATERMARK_ENABLED and media_type == "image":
        project_name = _project_name(db, scope.project_id)
        coord = "无定位" if (gps_lat is None or gps_lng is None) else f"{gps_lat:.6f},{gps_lng:.6f}"
        lines = [project_name, scope.user.name, local.strftime("%Y-%m-%d %H:%M:%S"), coord]
        wm_bytes = make_watermark_jpeg(tmp_path, lines)
        if wm_bytes is not None:
            file_key = f"{base_dir}/{uid}.jpg"
            mime = "image/jpeg"
            storage.put(file_key, wm_bytes, mime)
            watermarked = True

    width, height = image_dimensions(tmp_path) if media_type == "image" else (None, None)

    # 7. 落库
    att = Attachment(
        upload_key=upload_key,            gps_accuracy=accuracy,
        evidence={"client_shot_at": shot_at, "server_received_at": now.isoformat(),
                  "exif_time": exif.shot_time.isoformat() if exif.shot_time else None,
                  "client_lat": client_lat, "client_lng": client_lng,
                  "coordinate_source": "exif" if exif.gps_lat is not None else "client",
                  "coordinate_system": "wgs84" if exif.gps_lat is not None else coordinate_system,
                  "accuracy_m": accuracy, "reasons": reasons, "source_is_client_claim": True},
        org_id=scope.org_id,
        project_id=scope.project_id,
        biz_type=biz_type,
        biz_id=None,
        round=None,
        file_key=file_key,
        raw_key=raw_key,
        file_name=file_name,
        mime=mime,
        size=os.path.getsize(tmp_path),
        media_type=media_type,
        sha256=digest,
        width=width,
        height=height,
        exif_time=exif.shot_time,
        gps_lat=gps_lat,
        gps_lng=gps_lng,
        source=source,
        watermarked=watermarked,
        suspicious=suspicious,
        uploader_id=scope.user.id,
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    logger.info("附件上传 id=%s biz=%s user=%s sha256=%s suspicious=%s",
                att.id, biz_type, scope.user.id, digest[:12], suspicious)

    data = attachment_to_dict(att)
    if duplicate:
        data["hint"] = "疑似重复使用的照片"
    if suspicious and reasons:
        data["suspicious_reasons"] = reasons
    return data


def _project_name(db: Session, project_id: int) -> str:
    p = db.get(Project, project_id)
    return p.name if p else str(project_id)


# ---------- 元数据 ----------

@router.get("/{attachment_id}")
def attachment_meta(attachment_id: int, db: Session = Depends(get_db),
                    scope: ProjectScope = Depends(project_scope)):
    """附件元数据：含存证信息（拍摄时间/坐标/来源/水印/存证异常）"""
    att = _get_scoped_attachment(attachment_id, db, scope)
    return ok(attachment_to_dict(att))


# ---------- 鉴权下载 / 预览 ----------

def _unauthorized(msg: str = "登录已失效"):
    return HTTPException(status_code=401, detail={"code": 401, "msg": msg, "data": None})


def _attachment_sig(attachment_id: int, exp: int, user_id: int) -> str:
    """附件短时效签名（P0-3）：HMAC-SHA256(JWT_SECRET, attachment_id:exp:user_id)"""
    msg = f"{attachment_id}:{exp}:{user_id}".encode("utf-8")
    return hmac.new(settings.JWT_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()


@router.get("/{attachment_id}/signed-url")
def attachment_signed_url(attachment_id: int, db: Session = Depends(get_db),
                          scope: ProjectScope = Depends(project_scope)):
    """附件短时效签名 URL（P0-3，默认 SIGNED_URL_TTL_SECONDS=300s）：
    已登录用户经 Bearer 头鉴权（含可见性校验）换取 ?uid=&exp=&sig= 链接，
    供 <img>/wx.previewImage 等无法携带请求头的场景使用，替代原 ?access_token= 方案。
    云托管对象存储形态：file_id（cloud://…）可直接用于小程序 <image>/previewImage 组件，
    url 为微信侧短期 https 下载链接（浏览器/管理端直用）；两者均受可见性校验前置把关。"""
    att = _get_scoped_attachment(attachment_id, db, scope)
    wxc = get_wxcloud_storage()
    if wxc is not None:
        key = att.file_key if att.watermarked else att.raw_key
        try:
            url = wxc.presign(key, settings.SIGNED_URL_TTL_SECONDS)
        except (RuntimeError, ValueError) as exc:
            logger.warning("云存储签名链接获取失败 att=%s: %s", att.id, exc)
            raise BizError(1000, "附件链接获取失败，请稍后重试")
        return ok({
            "url": url,
            "file_id": wxc.file_id(key),
            "expires_in": settings.SIGNED_URL_TTL_SECONDS,
        })
    exp = int(time.time()) + settings.SIGNED_URL_TTL_SECONDS
    sig = _attachment_sig(att.id, exp, scope.user.id)
    return ok({
        "url": f"/api/attachments/{att.id}/file?uid={scope.user.id}&exp={exp}&sig={sig}",
        "expires_in": settings.SIGNED_URL_TTL_SECONDS,
    })


def _decode_token(token: str | None) -> dict:
    """解码 access token：缺失/无效 → 401（本路由独立于 get_current_user）"""
    if not token:
        raise _unauthorized()
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except JWTError:
        raise _unauthorized()


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _build_scope_for_user(user: User, att: Attachment, db: Session, pid: int | None) -> ProjectScope | None:
    """按用户与项目上下文构造 scope：项目不匹配或非成员（且非同组织管理员）→ None（403）"""
    if pid != att.project_id:
        return None
    project = db.get(Project, att.project_id)
    if project is None:
        return None
    memberships = get_membership(user, att.project_id, db)
    is_org_admin_here = bool(user.is_org_admin and user.org_id is not None and project.org_id == user.org_id)
    if not memberships and not is_org_admin_here:
        return None
    return ProjectScope(
        user=user, project_id=att.project_id, org_id=project.org_id,
        roles={m.role for m in memberships}, is_org_admin=is_org_admin_here, memberships=memberships,
    )


def _scope_from_signed_params(request: Request, db: Session, att: Attachment) -> ProjectScope | None:
    """P0-3 签名校验通道：?uid=&exp=&sig=（短时效，默认 300s）。
    exp 已过 / 签名不符 / 用户已停用 → 401；非附件项目成员 → None（403）。"""
    uid = request.query_params.get("uid")
    exp = request.query_params.get("exp")
    sig = request.query_params.get("sig")
    if not uid or not exp or not sig:
        raise _unauthorized()
    try:
        uid_int, exp_int = int(uid), int(exp)
    except (TypeError, ValueError):
        raise _unauthorized()
    if exp_int < time.time():
        raise _unauthorized("链接已过期，请刷新后重试")
    if not hmac.compare_digest(_attachment_sig(att.id, exp_int, uid_int), sig):
        raise _unauthorized()
    user = db.get(User, uid_int)
    if user is None or not user.active or user.status == "disabled":
        raise _unauthorized()
    # 签名签发时已校验可见性；此处按附件所属项目复核成员关系（用户可能已被移出项目）
    return _build_scope_for_user(user, att, db, att.project_id)


def _scope_for_file(request: Request, db: Session, att: Attachment) -> ProjectScope | None:
    """构造附件所属项目的 scope（P0-3 双通道）：
    1. Authorization Bearer 头（正常接口调用，X-Project-Id 头优先、回退 JWT pid）；
    2. ?uid=&exp=&sig= 短时效签名（<img> 标签场景）。
    令牌/签名缺失或无效/用户被停用 → 401；项目不匹配或非成员（且非同组织管理员）→ None（403）。"""
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        payload = _decode_token(auth[7:].strip())
        try:
            user_id = int(payload.get("sub"))
            tv = payload.get("tv")
        except (TypeError, ValueError):
            raise _unauthorized()
        user = db.get(User, user_id)
        if user is None or not user.active or user.status == "disabled":
            raise _unauthorized()
        if tv is None or int(tv) != user.token_version:
            raise _unauthorized()
        if user.must_change_password:
            raise HTTPException(status_code=401, detail={"code": 1060, "msg": "请先修改初始密码", "data": None})

        # 项目上下文：头优先（鉴权下载请求可带）；图片标签场景回退 JWT pid
        raw = request.headers.get("x-project-id")
        if raw is None or str(raw).strip() == "":
            raw = payload.get("pid")
        try:
            pid = int(raw) if raw is not None and str(raw).strip() != "" else None
        except (TypeError, ValueError):
            pid = None
        return _build_scope_for_user(user, att, db, pid)

    # 无 Bearer 头：走短时效签名校验（P0-3：?access_token= 回退已移除，令牌不得进 URL）
    return _scope_from_signed_params(request, db, att)


@router.get("/{attachment_id}/file")
def attachment_file(attachment_id: int, request: Request, thumb: int = 0,
                    db: Session = Depends(get_db)):
    """鉴权下载/预览：local 流式返回（Cache-Control private）；s3 302 到 ≤300s 签名 URL。
    鉴权双通道（P0-3）：Authorization Bearer 头，或 ?uid=&exp=&sig= 短时效签名（<img> 标签场景，
    经 /signed-url 换取）；?thumb=1 返回 512px 缩略图（服务端生成并缓存）。"""
    att = db.get(Attachment, attachment_id)
    if att is None or att.deleted_at is not None:
        raise HTTPException(status_code=404, detail={"code": 404, "msg": "附件不存在", "data": None})

    scope = _scope_for_file(request, db, att)
    if scope is None or not attachment_visible(att, scope, db):
        # 统一 403：不区分"非本项目"与"无业务可见性"，避免存在性泄露
        write_audit(db, "attachment_access_denied", target_type="attachment", target_id=att.id,
                    result="denied", org_id=att.org_id, project_id=att.project_id,
                    ip=_client_ip(request), user_agent=request.headers.get("user-agent"))
        raise HTTPException(status_code=403, detail={"code": 403, "msg": "无权访问该附件", "data": None})

    storage = get_storage()
    key = att.file_key if att.watermarked else att.raw_key

    # 缩略图：仅图片；生成后按 _thumb 键缓存于存储后端
    if thumb and att.media_type == "image":
        stem = key.rsplit(".", 1)[0]
        thumb_key = f"{stem}_thumb.jpg"
        if not storage.exists(thumb_key):
            data = make_thumbnail_jpeg_from_storage(storage, key)
            if data is None:
                thumb_key = None
            else:
                storage.put(thumb_key, data, "image/jpeg")
        if thumb_key:
            key = thumb_key

    presigned = storage.presign(key, settings.SIGNED_URL_TTL_SECONDS)
    if presigned:
        # s3 模式：302 到短期签名 URL（有效期 ≤ SIGNED_URL_TTL_SECONDS）
        return RedirectResponse(presigned, status_code=302)

    return StreamingResponse(
        storage.open_stream(key),
        media_type=att.mime,
        headers={"Cache-Control": "private, max-age=300"},
    )


def make_thumbnail_jpeg_from_storage(storage, key: str) -> bytes | None:
    """从存储对象生成缩略图（不经本地临时文件）"""
    import io

    from PIL import Image

    try:
        img = Image.open(io.BytesIO(storage.get(key))).convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    img.thumbnail((512, 512))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


# ---------- 删除（软删 + 固化保护） ----------

@router.delete("/{attachment_id}")
def delete_attachment(attachment_id: int, request: Request, db: Session = Depends(get_db),
                      scope: ProjectScope = Depends(project_scope)):
    """删除附件：仅上传者本人，且未绑定业务或所属业务尚未提交；已固化的证据一律 1043。
    删除为软删（原图保留 30 天再物理清理），并写审计（总纲 §4.9）。"""
    att = _get_scoped_attachment(attachment_id, db, scope)
    if att.uploader_id != scope.user.id:
        write_audit(db, "attachment_delete_denied", actor_id=scope.user.id, target_type="attachment",
                    target_id=att.id, result="denied", org_id=scope.org_id, project_id=scope.project_id,
                    ip=_client_ip(request), user_agent=request.headers.get("user-agent"))
        raise HTTPException(status_code=403, detail={"code": 403, "msg": "仅上传者可删除该附件", "data": None})

    # 固化判定：已绑定业务且业务已提交 → 证据不可灭失
    if att.biz_id is not None:
        if att.biz_type == "record":
            rec = db.get(InspectionRecord, att.biz_id)
            if rec is not None and rec.human_verdict is not None:
                raise BizError(1043, "该附件已作为证据固化，不可删除")
        else:
            # order_feedback 绑定即随反馈提交；order_extra 绑定即入单，均视为已固化
            raise BizError(1043, "该附件已作为证据固化，不可删除")

    att.deleted_at = now_utc()
    write_audit(db, "attachment_deleted", actor_id=scope.user.id, target_type="attachment",
                target_id=att.id, org_id=scope.org_id, project_id=scope.project_id,
                before={"file_key": att.file_key, "biz_type": att.biz_type, "biz_id": att.biz_id},
                ip=_client_ip(request), user_agent=request.headers.get("user-agent"), commit=False)
    db.commit()
    logger.info("附件软删 id=%s user=%s（对象保留 30 天待物理清理）", att.id, scope.user.id)
    return ok(None)
