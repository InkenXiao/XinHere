"""Markdown → PDF 转换（weasyprint + markdown），满足交付红线「MD+PDF 成对交付」。

用法（cwd=技能根目录，经 run_skill_script 执行）：
    python scripts/md2pdf.py reports/xxx.md            # 输出同目录同名 .pdf
    python scripts/md2pdf.py reports/xxx.md out.pdf    # 指定输出路径
"""
from __future__ import annotations

import sys
from pathlib import Path

_CSS = """
@page { size: A4; margin: 2cm 1.8cm; }
body {
    font-family: "Noto Sans CJK SC", "Noto Sans SC", "WenQuanYi Zen Hei", sans-serif;
    font-size: 10.5pt; line-height: 1.75; color: #1f2329;
}
h1 { font-size: 20pt; color: #0b1f3a; border-bottom: 2px solid #2563eb; padding-bottom: 8px; }
h2 { font-size: 15pt; color: #0b1f3a; border-left: 4px solid #2563eb; padding-left: 10px; margin-top: 22px; }
h3 { font-size: 12.5pt; color: #1d3557; margin-top: 16px; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 9.5pt; }
th, td { border: 1px solid #d0d5dd; padding: 6px 9px; text-align: left; }
th { background: #f2f6fc; color: #0b1f3a; }
tr:nth-child(even) { background: #fafbfc; }
code { font-family: "Noto Sans Mono", monospace; font-size: 9pt; background: #f4f5f7; padding: 1px 4px; border-radius: 3px; }
pre { background: #f4f5f7; padding: 10px; border-radius: 6px; white-space: pre-wrap; word-break: break-all; }
pre code { background: none; padding: 0; }
blockquote { border-left: 4px solid #94a3b8; margin: 10px 0; padding: 4px 12px; color: #475569; background: #f8fafc; }
img { max-width: 100%; }
a { color: #2563eb; text-decoration: none; }
"""


def main() -> int:
    args = [a for a in sys.argv[1:] if a.strip()]
    if not args:
        print("用法: python scripts/md2pdf.py <input.md> [output.pdf]")
        return 2
    src = Path(args[0])
    if not src.is_file():
        print(f"输入文件不存在: {src}")
        return 2
    dst = Path(args[1]) if len(args) > 1 else src.with_suffix(".pdf")

    import markdown
    from weasyprint import HTML

    text = src.read_text(encoding="utf-8")
    body = markdown.markdown(text, extensions=["tables", "fenced_code"])
    html = f"<html><head><meta charset='utf-8'><style>{_CSS}</style></head><body>{body}</body></html>"
    dst.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(Path.cwd())).write_pdf(str(dst))
    size = dst.stat().st_size
    print(f"PDF 已生成: {dst} ({size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
