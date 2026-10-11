"""PPT 渲染器：PptSpec 中间表示 → .pptx 字节（python-pptx，A2 代码化版式库）。

设计要点：
- 中间表示（PptSpec）与渲染分离：spec 是纯数据（pydantic 校验），render 是纯函数
  （spec → bytes，无 IO/无 LLM），可 golden 单测；后续 Word 渲染器可复用同一份 spec。
- 版式库：cover（封面）/ agenda（目录）/ section（章节页）/ bullets（要点页）/
  table（表格页）/ end（结尾页）；16:9；中文字体统一（latin+ea 双设，避免 Linux 容器乱码）。
- 品牌色与 ui 设计稿一致：主橙 #FA8A39 / 深橙 #D94720 / 墨色 #241B13。
"""
from __future__ import annotations

import io
from typing import Literal

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt
from pydantic import BaseModel, Field

# —— 品牌色板（对齐 ui/ 新首页视觉：橙主色 + 墨色文字） ——
ORANGE = RGBColor(0xFA, 0x8A, 0x39)
ORANGE_DEEP = RGBColor(0xD9, 0x47, 0x20)
INK = RGBColor(0x24, 0x1B, 0x13)
INK_SOFT = RGBColor(0x5C, 0x4A, 0x3A)
PAPER = RGBColor(0xFF, 0xFD, 0xF8)
ORANGE_SOFT = RGBColor(0xFD, 0xF0, 0xE5)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

FONT = "微软雅黑"

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)


class TableSpec(BaseModel):
    headers: list[str] = Field(min_length=1)
    rows: list[list[str]] = Field(default_factory=list)


class SlideSpec(BaseModel):
    layout: Literal["cover", "agenda", "section", "bullets", "table", "end"]
    title: str
    subtitle: str = ""
    bullets: list[str] = Field(default_factory=list)
    table: TableSpec | None = None
    notes: str = ""


class PptSpec(BaseModel):
    title: str
    subtitle: str = ""
    slides: list[SlideSpec] = Field(min_length=1)


# —— 基础绘制 ————————————————————————————————————————


def _blank(prs: Presentation):
    return prs.slides.add_slide(prs.slide_layouts[6])  # 6 = blank


def _style_run(run, *, size: int, bold: bool = False, color: RGBColor = INK) -> None:
    f = run.font
    f.name = FONT
    f.size = Pt(size)
    f.bold = bold
    f.color.rgb = color
    # latin 之外补 east-asian 字体，否则 PowerPoint 里中文回退默认字体
    rPr = run._r.get_or_add_rPr()
    ea = rPr.find(qn("a:ea"))
    if ea is None:
        ea = rPr.makeelement(qn("a:ea"), {})
        rPr.append(ea)
    ea.set("typeface", FONT)


def _textbox(slide, x: Emu, y: Emu, w: Emu, h: Emu, text: str, *,
             size: int, bold: bool = False, color: RGBColor = INK,
             align: PP_ALIGN = PP_ALIGN.LEFT, anchor: MSO_ANCHOR = MSO_ANCHOR.TOP,
             line_gap: float = 1.15) -> None:
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = line_gap
        r = p.add_run()
        r.text = ln
        _style_run(r, size=size, bold=bold, color=color)


def _rect(slide, x: Emu, y: Emu, w: Emu, h: Emu, fill: RGBColor) -> None:
    from pptx.enum.shapes import MSO_SHAPE

    shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    shp.line.fill.background()
    shp.shadow.inherit = False


def _lamp_color(name: str) -> RGBColor:
    return {"红": RGBColor(0xE1, 0x1D, 0x48), "黄": RGBColor(0xF5, 0x9E, 0x0B),
            "绿": RGBColor(0x16, 0xA3, 0x4A)}.get(name, INK_SOFT)


# —— 各版式 ————————————————————————————————————————


def _slide_cover(prs: Presentation, s: SlideSpec, spec: PptSpec) -> None:
    slide = _blank(prs)
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, PAPER)
    _rect(slide, 0, Inches(6.9), SLIDE_W, Inches(0.6), ORANGE)
    _rect(slide, Inches(0.9), Inches(2.2), Inches(0.14), Inches(1.7), ORANGE_DEEP)
    _textbox(slide, Inches(1.3), Inches(2.2), Inches(11), Inches(1.8), s.title,
             size=40, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    if s.subtitle or spec.subtitle:
        _textbox(slide, Inches(1.3), Inches(4.1), Inches(11), Inches(0.8),
                 s.subtitle or spec.subtitle, size=18, color=INK_SOFT)
    _textbox(slide, Inches(1.3), Inches(5.9), Inches(11), Inches(0.5),
             s.notes, size=14, color=INK_SOFT)


def _slide_section(prs: Presentation, s: SlideSpec) -> None:
    slide = _blank(prs)
    _rect(slide, 0, 0, Inches(4.6), SLIDE_H, ORANGE)
    _textbox(slide, Inches(0.7), Inches(2.8), Inches(3.6), Inches(1.9), s.title,
             size=30, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
    if s.subtitle:
        _textbox(slide, Inches(5.2), Inches(3.2), Inches(7.4), Inches(1.1), s.subtitle,
                 size=16, color=INK_SOFT)


def _slide_bullets(prs: Presentation, s: SlideSpec) -> None:
    slide = _blank(prs)
    _rect(slide, 0, 0, SLIDE_W, Inches(0.18), ORANGE)
    _textbox(slide, Inches(0.9), Inches(0.55), Inches(11.6), Inches(0.9), s.title,
             size=26, bold=True)
    top = Inches(1.9)
    step = min(int(Inches(0.62)), int((Inches(6.9) - top) // max(len(s.bullets), 1)))
    for i, b in enumerate(s.bullets):
        y = top + step * i
        dot = slide.shapes.add_textbox(Inches(0.95), y, Inches(0.4), Inches(0.5))
        r = dot.text_frame.paragraphs[0].add_run()
        r.text = "▪"
        _style_run(r, size=16, bold=True, color=ORANGE)
        # 首个「·」后是名称（企业/指标），着墨色加粗由 LLM 无关：整体一段式
        _textbox(slide, Inches(1.45), y, Inches(11), Inches(0.55), b, size=16)


def _slide_table(prs: Presentation, s: SlideSpec) -> None:
    slide = _blank(prs)
    _rect(slide, 0, 0, SLIDE_W, Inches(0.18), ORANGE)
    _textbox(slide, Inches(0.9), Inches(0.55), Inches(11.6), Inches(0.9), s.title,
             size=26, bold=True)
    if s.table is None:
        return
    headers, rows = s.table.headers, s.table.rows
    n_rows = min(len(rows), 14)  # 版面保护：超 14 行截断
    shape = slide.shapes.add_table(n_rows + 1, len(headers),
                                   Inches(0.9), Inches(1.8), Inches(11.5), Inches(0.5 + 0.45 * (n_rows + 1)))
    tbl = shape.table
    for j, h in enumerate(headers):
        cell = tbl.cell(0, j)
        cell.fill.solid()
        cell.fill.fore_color.rgb = ORANGE
        p = cell.text_frame.paragraphs[0]
        r = p.add_run()
        r.text = h
        _style_run(r, size=14, bold=True, color=WHITE)
    for i, row in enumerate(rows[:n_rows], start=1):
        for j in range(len(headers)):
            val = row[j] if j < len(row) else ""
            cell = tbl.cell(i, j)
            cell.fill.solid()
            cell.fill.fore_color.rgb = PAPER if i % 2 else ORANGE_SOFT
            p = cell.text_frame.paragraphs[0]
            r = p.add_run()
            r.text = val
            # 灯色列（红/黄/绿计数或文字）按语义着色，其余墨色
            color = _lamp_color(val) if val in ("红", "黄", "绿") else INK
            _style_run(r, size=13, color=color, bold=val in ("红", "黄", "绿"))
    if s.notes:
        _textbox(slide, Inches(0.9), Inches(6.7), Inches(11.5), Inches(0.5), s.notes,
                 size=12, color=INK_SOFT)


def _slide_agenda(prs: Presentation, s: SlideSpec) -> None:
    slide = _blank(prs)
    _rect(slide, 0, 0, SLIDE_W, Inches(0.18), ORANGE)
    _textbox(slide, Inches(0.9), Inches(0.55), Inches(11.6), Inches(0.9), s.title,
             size=26, bold=True)
    top = Inches(2.0)
    step = min(int(Inches(0.72)), int((Inches(6.8) - top) // max(len(s.bullets), 1)))
    for i, b in enumerate(s.bullets):
        y = top + step * i
        num = slide.shapes.add_textbox(Inches(1.0), y, Inches(0.8), Inches(0.6))
        r = num.text_frame.paragraphs[0].add_run()
        r.text = f"{i + 1:02d}"
        _style_run(r, size=22, bold=True, color=ORANGE)
        _textbox(slide, Inches(2.0), y + Inches(0.05), Inches(10), Inches(0.6), b, size=18)


def _slide_end(prs: Presentation, s: SlideSpec) -> None:
    slide = _blank(prs)
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, ORANGE)
    _textbox(slide, 0, Inches(3.0), SLIDE_W, Inches(1.4), s.title or "谢谢",
             size=36, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    if s.subtitle:
        _textbox(slide, 0, Inches(4.5), SLIDE_W, Inches(0.7), s.subtitle,
                 size=16, color=WHITE, align=PP_ALIGN.CENTER)


_RENDERERS = {
    "cover": _slide_cover,
    "agenda": _slide_agenda,
    "section": _slide_section,
    "bullets": _slide_bullets,
    "table": _slide_table,
    "end": _slide_end,
}


def render(spec: PptSpec) -> bytes:
    """PptSpec → .pptx 字节（纯函数，无 IO）。"""
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    for s in spec.slides:
        if s.layout == "cover":
            _slide_cover(prs, s, spec)
        else:
            _RENDERERS[s.layout](prs, s)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
