# 报表 PDF 生成引擎：巡查报告 / 整改报告 / 项目汇总报告
# 面向政府抽查、竣工验收、集团汇报三类对正式性、可导出、可盖章留痕有要求的场景。
# 字体采用 reportlab 内置 CID 字体 STSong-Light（无需额外字体文件，跨平台中文可用）；
# 公章以矢量方式绘制，报告末尾附「存证信息」（报告编号 / 生成人 / 生成时间 / 内容指纹），
# 生成动作本身在路由层写审计日志（audit_logs），实现留痕闭环。
import io
import math
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ---------- 业务枚举中文映射（与 admin-web/src/utils/dict.js 保持一致） ----------
STATUS_LABEL = {
    "pending": "待接收", "accepted": "已接收", "processing": "整改中",
    "review": "待审核", "closed": "已闭环", "cancelled": "已作废",
}
VERDICT_LABEL = {"normal": "正常", "abnormal": "异常"}
RISK_LABEL = {"high": "高", "medium": "中", "low": "低"}
CATEGORY_LABEL = {
    "structural": "结构变形", "protection": "防护缺失",
    "installation": "安装不规范", "material": "材料质量", "safety": "安全隐患",
}
ACTION_LABEL = {
    "create": "创建工单", "accept": "接收工单", "start": "开始整改",
    "feedback": "提交整改反馈", "review_pass": "审核通过", "review_reject": "审核打回",
    "urge": "催办", "escalate": "升级",
}

_CJK = "STSong-Light"
_THEME = colors.HexColor("#1E5FD0")
_TEXT = colors.HexColor("#1a2634")
_MUTE = colors.HexColor("#5b6b7d")
_LINE = colors.HexColor("#e4e7ed")
_SEAL_RED = colors.HexColor("#C8102E")

# A4 可用宽度（210mm - 左右边距各 18mm）
_USABLE = A4[0] - 36 * mm


def _register_fonts() -> None:
    """注册中文字体；STSong-Light 无加粗变体，注册家族避免 <b> 触发缺字形异常。"""
    if _CJK not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(_CJK))
    pdfmetrics.registerFontFamily(_CJK, normal=_CJK, bold=_CJK, italic=_CJK, boldItalic=_CJK)


_register_fonts()


def _style(name, **kw):
    defaults = dict(fontName=_CJK, textColor=_TEXT)
    defaults.update(kw)
    return ParagraphStyle(name, **defaults)


STYLES = {
    "title": _style("title", fontSize=18, leading=26, alignment=TA_CENTER),
    "subtitle": _style("subtitle", fontSize=9, leading=14, alignment=TA_CENTER, textColor=_MUTE),
    "h2": _style("h2", fontSize=12.5, leading=17, textColor=_THEME),
    "body": _style("body", fontSize=10.5, leading=18),
    "label": _style("label", fontSize=9, leading=14, textColor=_MUTE),
    "value": _style("value", fontSize=9.5, leading=14),
    "cell": _style("cell", fontSize=9, leading=13),
    "small": _style("small", fontSize=8, leading=12, textColor=_MUTE),
}


def _esc(text) -> str:
    return _xml_escape("" if text is None else str(text))


def _br(text) -> str:
    return _esc(text).replace("\n", "<br/>")


def _disp(value) -> str:
    return "—" if value in (None, "") else str(value)


def _chunk(text: str, size: int) -> list[str]:
    text = (text or "").strip()
    return [text] if not text else [text[i:i + size] for i in range(0, len(text), size)]


def _header_footer(meta: dict):
    """页眉（组织名 / 报告编号 + 主题色分隔线）与页脚（留痕提示 + 页号）。"""

    def draw(canv, doc):
        canv.saveState()
        w, h = A4
        lm, rm = 18 * mm, 18 * mm
        canv.setStrokeColor(_THEME)
        canv.setLineWidth(1)
        canv.line(lm, h - 13 * mm, w - rm, h - 13 * mm)
        canv.setFont(_CJK, 8.5)
        canv.setFillColor(_MUTE)
        canv.drawString(lm, h - 11.5 * mm, meta.get("org_name") or "")
        canv.drawRightString(w - rm, h - 11.5 * mm, meta.get("report_no") or "")
        # 页脚
        canv.line(lm, 9 * mm, w - rm, 9 * mm)
        canv.setFont(_CJK, 7.5)
        canv.drawString(lm, 5 * mm, "报告由「工程现场智能巡查助手」生成，末页含电子存证信息")
        canv.drawRightString(w - rm, 5 * mm, f"第 {doc.page} 页")
        canv.restoreState()

    return draw


def render_report(meta: dict, story: list) -> bytes:
    """把 flowable 列表渲染为 PDF 字节并返回。"""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=20 * mm, bottomMargin=18 * mm,
        title=meta.get("report_title") or "巡查报告",
        author=meta.get("generated_by") or meta.get("org_name") or "",
        onFirstPage=_header_footer(meta),
        onLaterPages=_header_footer(meta),
    )
    doc.build(story)
    return buf.getvalue()


# ---------- 公文章 ----------

class _Seal(Flowable):
    """矢量公章：双红圈 + 五角星 + 组织名 + 「电子专用章」。"""

    def __init__(self, org_name: str, size: float = 34 * mm):
        super().__init__()
        self.org_name = (org_name or "").strip() or "建设单位"
        self.size = float(size)

    def wrap(self, availWidth, availHeight):
        self.width = self.height = min(self.size, availWidth)
        return self.width, self.height

    def draw(self):
        c = self.canv
        d = self.width
        cx = cy = d / 2.0
        c.saveState()
        c.setStrokeColor(_SEAL_RED)
        c.setFillColor(_SEAL_RED)
        c.setLineWidth(d * 0.028)
        c.circle(cx, cy, d / 2 - d * 0.028, stroke=1, fill=0)
        c.setLineWidth(d * 0.014)
        c.circle(cx, cy, d / 2 - d * 0.07, stroke=1, fill=0)
        # 五角星
        p = c.beginPath()
        pts = self._star_points(cx, cy + d * 0.05, d * 0.30, d * 0.12)
        p.moveTo(pts[0][0], pts[0][1])
        for x, y in pts[1:]:
            p.lineTo(x, y)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
        # 组织名（最多两行，每行 4 字）
        c.setFont(_CJK, d * 0.145)
        lines = _chunk(self.org_name, 4)
        if len(lines) > 2:
            lines = lines[:2]
            lines[1] = lines[1][:3] + "…"
        for i, ln in enumerate(lines):
            c.drawCentredString(cx, cy - d * 0.38 - i * d * 0.17, ln)
        c.setFont(_CJK, d * 0.13)
        c.drawCentredString(cx, cy - d * 0.68, "电子专用章")
        c.restoreState()

    @staticmethod
    def _star_points(cx, cy, R, r):
        pts = []
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            rad = R if i % 2 == 0 else r
            pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
        return pts


# ---------- 构建辅助 ----------

def title_block(meta: dict) -> list:
    org = meta.get("org_name") or ""
    proj = meta.get("project_name") or ""
    title = meta.get("report_title") or "巡查报告"
    return [
        Spacer(1, 4 * mm),
        Paragraph(_esc(title), STYLES["title"]),
        Spacer(1, 2 * mm),
        Paragraph(_esc(f"{org} · {proj}"), STYLES["subtitle"]),
        Spacer(1, 2 * mm),
        Paragraph(_esc(f"报告编号：{meta.get('report_no') or '—'}　生成时间：{meta.get('generated_at') or '—'}"),
                  STYLES["subtitle"]),
        Spacer(1, 6 * mm),
    ]


def heading(text: str) -> list:
    return [
        KeepTogether([
            Paragraph(_esc(text), STYLES["h2"]),
            HRFlowable(width="100%", thickness=0.8, color=_THEME, spaceBefore=1, spaceAfter=2),
        ]),
        Spacer(1, 2 * mm),
    ]


def info_table(pairs: list[tuple[str, str]]) -> Flowable:
    """两列键值信息栏（短字段）。长文本请用 kv_para。"""
    col = 2
    cells = []
    for label, value in pairs:
        cells.append(Paragraph(_esc(label), STYLES["label"]))
        cells.append(Paragraph(_br(_disp(value)), STYLES["value"]))
    while len(cells) % (col * 2):
        cells.append(Paragraph("", STYLES["label"]))
        cells.append(Paragraph("", STYLES["value"]))
    lab_w, val_w = 26 * mm, (_USABLE / 2 - 26 * mm)
    rows = [cells[i:i + col * 2] for i in range(0, len(cells), col * 2)]
    t = Table(rows, colWidths=[lab_w, val_w, lab_w, val_w])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("BOX", (0, 0), (-1, -1), 0.5, _LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, _LINE),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f5f8fd")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f5f8fd")),
    ]))
    return t


def kv_para(label: str, value) -> Paragraph:
    return Paragraph(
        f'<font color="#5b6b7d">{_esc(label)}：</font>{_br(_disp(value))}',
        STYLES["body"],
    )


def bullets(items: list[str]) -> list:
    out = []
    for it in items:
        out.append(Paragraph(f'<font color="#1E5FD0">·</font>&nbsp;{_br(_disp(it))}', STYLES["body"]))
    return out or [Paragraph("—", STYLES["body"])]


def _photo_bytes(storage, att) -> bytes | None:
    try:
        key = att.file_key if att.watermarked else att.raw_key
        return storage.get(key)
    except Exception:
        return None


def image_grid(storage, atts, cols: int = 2) -> Flowable:
    """现场照片网格（按比例缩放、带文件名说明）。"""
    cell_w = _USABLE / cols
    cells = []
    for att in atts:
        data = _photo_bytes(storage, att)
        block = []
        if data:
            try:
                ir = ImageReader(io.BytesIO(data))
                iw, ih = ir.getSize()
                if iw and ih:
                    h = min((cell_w - 8 * mm) * ih / iw, 62 * mm)
                    w = h * iw / ih
                    block.append(Image(ir, width=w, height=h))
                    block.append(Paragraph(_esc(att.file_name or f"照片 {att.id}"), STYLES["small"]))
                else:
                    block.append(Paragraph("（图片无法读取）", STYLES["small"]))
            except Exception:
                block.append(Paragraph("（图片无法读取）", STYLES["small"]))
        else:
            block.append(Paragraph("（图片缺失）", STYLES["small"]))
        cells.append(block)
    while len(cells) % cols:
        cells.append([Paragraph("", STYLES["small"])])
    rows = [cells[i:i + cols] for i in range(0, len(cells), cols)]
    t = Table(rows, colWidths=[cell_w] * cols)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("BOX", (0, 0), (-1, -1), 0.5, _LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, _LINE),
    ]))
    return t


def evidence_block(meta: dict) -> Flowable:
    """存证信息块 + 公章（盖章留痕）。"""
    left = [
        Paragraph(_esc("存证信息"), STYLES["h2"]),
        Spacer(1, 1 * mm),
        kv_para("报告编号", meta.get("report_no")),
        kv_para("生成人", meta.get("generated_by")),
        kv_para("生成时间", meta.get("generated_at")),
        Paragraph(_esc(f'<font color="#5b6b7d">内容指纹：</font>{meta.get("fingerprint", "—")}'), STYLES["small"]),
        Spacer(1, 2 * mm),
        Paragraph(
            _esc("本报告由系统依据业务数据自动生成，内容与源数据一致；盖章仅限电子存证用途，"
                 "如需纸质归档请打印后加盖实体公章。"),
            STYLES["small"],
        ),
    ]
    inner = Table([[left, _Seal(meta.get("org_name") or "")]],
                  colWidths=[_USABLE - 44 * mm, 40 * mm])
    inner.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("LEFTPADDING", (0, 0), (0, 0), 8),
        ("RIGHTPADDING", (0, 0), (0, 0), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BOX", (0, 0), (-1, -1), 1, _THEME),
    ]))
    return inner


def data_table(header: list[str], rows: list[list[str]], col_widths: list[float] | None = None) -> Flowable:
    """通用明细表格：表头主题色底 + 数据行。"""
    n = len(header)
    if col_widths is None:
        col_widths = [_USABLE / n] * n
    head = [Paragraph(f'<font color="#ffffff">{_esc(h)}</font>',
                      _style("th", fontSize=9, leading=13, textColor=colors.white)) for h in header]
    body = []
    for r in rows:
        body.append([Paragraph(_br(_disp(v)), STYLES["cell"]) for v in r])
    t = Table([head] + body, colWidths=col_widths, repeatRows=1)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("BACKGROUND", (0, 0), (-1, 0), _THEME),
        ("BOX", (0, 0), (-1, -1), 0.5, _LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, _LINE),
    ]
    for i, _ in enumerate(body):
        if i % 2 == 1:
            style.append(("BACKGROUND", (0, i + 1), (-1, i + 1), colors.HexColor("#f5f8fd")))
    t.setStyle(TableStyle(style))
    return t