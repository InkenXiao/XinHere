#!/usr/bin/env python3
"""下载声纹识别模型（3D-Speaker CAM++ ONNX，说话人验证，输出 192 维 embedding）。

用法（在 apps/backend 下）：
  .venv/Scripts/python scripts/download_voiceprint_model.py
  .venv/Scripts/python scripts/download_voiceprint_model.py --url <直链>   # 内网镜像覆盖

落盘：data/models/campplus.onnx（.gitignore 已忽略 data/，模型不入库）。
模型文件缺失时后端自动降级：听写/会议不受影响，字幕回落「员工」/「说话人N」。
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
TARGET = BACKEND_DIR / "data" / "models" / "campplus.onnx"

# sherpa-onnx 官方转换的 3D-Speaker CAM++ 说话人验证 ONNX（16kHz 中文，192 维）；
# 注意 release tag 是官方原文的 speaker-recongition-models（sherpa-onnx 文档自带拼写，非笔误）；
# 直连 GitHub 不通时可换镜像前缀或手动下载后放到 data/models/campplus.onnx
_ASSET = "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"
DEFAULT_URLS = [
    f"https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/{_ASSET}",
    f"https://mirror.ghproxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/{_ASSET}",
]


def download(url: str, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".part")
    print(f"下载：{url}")
    with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r{done / 1e6:.1f} / {total / 1e6:.1f} MB", end="", flush=True)
    print()
    tmp.replace(dst)
    print(f"完成：{dst}（{dst.stat().st_size / 1e6:.1f} MB）")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", help="覆盖默认下载直链")
    args = ap.parse_args()

    if TARGET.exists() and TARGET.stat().st_size > 1_000_000:
        print(f"已存在：{TARGET}，跳过下载")
        return 0

    urls = [args.url] if args.url else DEFAULT_URLS
    last_err: Exception | None = None
    for url in urls:
        try:
            download(url, TARGET)
            return 0
        except Exception as e:  # noqa: BLE001 换下一个源
            last_err = e
            print(f"下载失败：{e}", file=sys.stderr)
    print(
        "\n全部源下载失败。请在能访问外网的机器下载后手动放置：\n"
        f"  {TARGET}\n（任选一个默认源，或用 --url 指定内网镜像直链）\n"
        f"最后错误：{last_err}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
