# 图像存证服务：magic bytes 校验、SHA256、EXIF/GPS 提取、拍摄软件反作弊、水印叠加、缩略图（P04 §2.3/§6.3）
import hashlib
import io
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from ..config import settings

logger = logging.getLogger(__name__)

# 真实类型白名单：real_ext -> (mime, media_type)；jpg 兼容 jpeg
ALLOWED_TYPES: dict[str, tuple[str, str]] = {
    "jpg": ("image/jpeg", "image"),
    "png": ("image/png", "image"),
    "webp": ("image/webp", "image"),
    "mp3": ("audio/mpeg", "audio"),
    "m4a": ("audio/m4a", "audio"),
    "mp4": ("video/mp4", "video"),
}
# 声明扩展名 -> 归一化真实扩展名（jpeg 视作 jpg）
_EXT_ALIAS = {"jpeg": "jpg"}
# 允许客户端声明的 MIME（第一重校验）；magic bytes 仍为最终判定（双重验证之第二重）
ALLOWED_MIMES = {mime for mime, _ in ALLOWED_TYPES.values()}
# 编辑/截图软件特征（EXIF Software 字段），命中即视为非原始拍摄
_EDIT_SOFTWARE_HINTS = ("photoshop", "美图", "meitu", "snapseed", "lightroom", "gimp", "picsart", "screenshot", "faststone")


def detect_real_type(path: str, filename: str) -> tuple[str, str, str] | None:
    """magic bytes 识别真实类型并校验与扩展名一致。
    返回 (real_ext, mime, media_type)；类型不在白名单或与扩展名不一致返回 None（→ 1042）。"""
    import filetype

    kind = filetype.guess(path)
    if kind is None:
        return None
    real_ext = _EXT_ALIAS.get(kind.extension, kind.extension)
    if real_ext not in ALLOWED_TYPES:
        return None
    declared = os.path.splitext(filename or "")[1].lower().lstrip(".")
    declared = _EXT_ALIAS.get(declared, declared)
    # 扩展名必须与真实类型一致（消灭 C2 伪装）
    if declared and declared != real_ext:
        return None
    mime, media_type = ALLOWED_TYPES[real_ext]
    return real_ext, mime, media_type


def validate_mime(mime: str | None) -> bool:
    """客户端声明 MIME 白名单校验（第一重，含 jpeg/jpg 别名归一），防伪装文件类型。"""
    if not mime:
        return False
    declared = mime.split(";")[0].strip().lower()
    if declared in ("image/jpg", "image/jpeg"):
        return True
    return declared in ALLOWED_MIMES


def mime_consistent(declared_mime: str | None, detected_mime: str) -> bool:
    """客户端声明 MIME 与 magic bytes 检测 MIME 一致性（第二重交叉验证）。
    无声明（云托管通道）不校验一致性——magic bytes 已兜底。"""
    if not declared_mime:
        return True
    declared = declared_mime.split(";")[0].strip().lower()
    detected = detected_mime.split(";")[0].strip().lower()
    if declared == detected:
        return True
    # jpeg/jpg 别名互认
    return {declared, detected} == {"image/jpeg", "image/jpg"}


def sha256_file(path: str) -> str:
    """流式计算文件 SHA256（不整文件读入内存）"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.APP_TIMEZONE)
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Shanghai")


def _ratios_to_decimal(vals, ref: bytes | str | None) -> Decimal | None:
    """EXIF GPS 度分秒有理数 → 十进制度；ref 为 S/W 时取负"""
    try:
        d = Decimal(vals[0][0]) / Decimal(vals[0][1])
        m = Decimal(vals[1][0]) / Decimal(vals[1][1])
        s = Decimal(vals[2][0]) / Decimal(vals[2][1])
        dec = d + m / 60 + s / 3600
    except (IndexError, ZeroDivisionError, TypeError, ValueError):
        return None
    r = ref.decode() if isinstance(ref, bytes) else (ref or "")
    if r.upper() in ("S", "W"):
        dec = -dec
    return dec.quantize(Decimal("0.000001"))


class ExifInfo:
    """EXIF 存证信息载体"""

    def __init__(self):
        self.shot_time: datetime | None = None      # aware UTC
        self.gps_lat: Decimal | None = None
        self.gps_lng: Decimal | None = None
        self.software: str = ""
        self.make: str = ""
        self.model: str = ""


def extract_exif(path: str, media_type: str) -> ExifInfo:
    """提取 JPEG EXIF 的拍摄时间 / GPS / 相机与软件字段；无 EXIF 或非 JPEG 返回空载体。"""
    info = ExifInfo()
    if media_type != "image":
        return info
    try:
        import piexif

        exif = piexif.load(path)
    except Exception:  # noqa: BLE001  非 JPEG / 无 EXIF
        return info

    zeroth = exif.get("0th", {})
    ex = exif.get("Exif", {})
    gps = exif.get("GPS", {})

    def _s(d, tag) -> str:
        v = d.get(tag)
        if isinstance(v, bytes):
            return v.decode(errors="ignore").strip("\x00").strip()
        return str(v).strip() if v is not None else ""

    info.software = _s(zeroth, piexif.ImageIFD.Software)
    info.make = _s(zeroth, piexif.ImageIFD.Make)
    info.model = _s(zeroth, piexif.ImageIFD.Model)

    raw_dt = ex.get(piexif.ExifIFD.DateTimeOriginal) or zeroth.get(piexif.ImageIFD.DateTime)
    if raw_dt:
        text = raw_dt.decode(errors="ignore") if isinstance(raw_dt, bytes) else str(raw_dt)
        try:
            naive = datetime.strptime(text.strip(), "%Y:%m:%d %H:%M:%S")
            # EXIF 无时区，按项目时区解释后转 UTC 存储
            info.shot_time = naive.replace(tzinfo=_tz()).astimezone(timezone.utc)
        except ValueError:
            pass

    if gps:
        info.gps_lat = _ratios_to_decimal(gps.get(piexif.GPSIFD.GPSLatitude), gps.get(piexif.GPSIFD.GPSLatitudeRef))
        info.gps_lng = _ratios_to_decimal(gps.get(piexif.GPSIFD.GPSLongitude), gps.get(piexif.GPSIFD.GPSLongitudeRef))
    return info


def is_edited_software(info: ExifInfo, media_type: str) -> bool:
    """判断照片是否疑似编辑/截图生成：软件字段命中特征，或图片无任何相机 Make/Model（P04 §2.4.3）。"""
    if media_type != "image":
        return False
    sw = (info.software or "").lower()
    if any(h in sw for h in _EDIT_SOFTWARE_HINTS):
        return True
    # 无相机来源信息（既无 Make 又无 Model）：无法佐证为现场拍摄
    if not info.make and not info.model:
        return True
    return False


def make_watermark_jpeg(raw_path: str, lines: list[str]) -> bytes | None:
    """在图片右下角叠加半透明文字块（项目/拍摄人/接收时间/坐标）。
    水印块占高 ≤8%，字号随图宽自适应；返回 JPEG 字节，失败返回 None。"""
    from PIL import Image, ImageDraw, ImageFont

    try:
        img = Image.open(raw_path).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        logger.warning("水印生成失败（无法打开图片）：%s", exc)
        return None

    w, h = img.size
    text = "  ".join(lines)
    font_size = max(12, int(w * 0.022))
    font = _load_font(font_size)

    draw = ImageDraw.Draw(img, "RGBA")
    # 文本尺寸
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:  # noqa: BLE001
        tw, th = int(w * 0.6), font_size
    pad = max(6, int(font_size * 0.4))
    block_h = min(th + pad * 2, int(h * 0.08) + pad * 2)  # 占高 ≤8%（含内边距）
    y0 = h - block_h
    # 半透明黑底
    draw.rectangle([0, y0, w, h], fill=(0, 0, 0, 110))
    draw.text((pad, y0 + (block_h - th) / 2), text, font=font, fill=(255, 255, 255, 230))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def make_thumbnail_jpeg(raw_path: str, max_size: int = 512) -> bytes | None:
    """生成不超过 max_size 的缩略图 JPEG 字节；非图片或失败返回 None。"""
    from PIL import Image

    try:
        img = Image.open(raw_path).convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    img.thumbnail((max_size, max_size))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def image_dimensions(raw_path: str) -> tuple[int | None, int | None]:
    from PIL import Image

    try:
        with Image.open(raw_path) as img:
            return img.width, img.height
    except Exception:  # noqa: BLE001
        return None, None


def _load_font(size: int):
    """尽量加载可渲染中文的字体，回退到 PIL 默认位图字体。"""
    from PIL import ImageFont

    candidates = [
        "C:/Windows/Fonts/msyh.ttc",      # 微软雅黑
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()
