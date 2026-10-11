"""大模型 ASR 网关共用通道：对话输入区语音转写与会议分片转写共用。

网关约定：16kHz 16-bit 单声道 WAV，multipart 上传，model=ASR，
返回 {text: <原始转写>}；原始文本需经 clean_asr_transcript 清洗后再使用。
"""
from __future__ import annotations

import logging
import re

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)


def clean_asr_transcript(raw_text: str) -> str:
    """清洗 ASR 网关原始返回：
    1. 剥离 language 标识、特殊标签与 <asr_text> 标记；
    2. 抑制静音/底噪下 Whisper 大模型循环幻觉（如持续重复的 "No, no, no...", "you you you..." 等）。
    """
    cleaned = (raw_text or "").strip()
    cleaned = re.sub(r"<\|[^>]*\|>", "", cleaned).strip()
    cleaned = re.sub(r"^language [^<]*", "", cleaned).strip()
    cleaned = re.sub(r"</?asr_text>", "", cleaned).strip()

    # 1. 切分为归一化 tokens（统一小写、剥离标点）
    tokens = [t for t in re.split(r"[\s,，。!！?？、；;]+", cleaned.lower()) if t]
    if len(tokens) >= 4:
        # 计算不同 token 占总 token 的比例
        unique_ratio = len(set(tokens)) / len(tokens)
        # 若总词数 >= 4 且独特词占比 <= 0.35（例如全是 "no", "you", "好的" 循环），判定为静音幻觉
        if unique_ratio <= 0.35:
            return ""

    # 2. 折叠连续重复词（如 "好的，好的，好的，好的，我们马上开始" -> "好的，我们马上开始"）
    cleaned = re.sub(r"([\w\u4e00-\u9fa5]+[\s,，。!！?？]+)\1{2,}", r"\1", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"([\w\u4e00-\u9fa5]+)\s+\1(\s+\1)+", r"\1", cleaned, flags=re.IGNORECASE)

    return cleaned.strip()


async def transcribe_audio_chunk(audio_bytes: bytes) -> tuple[str, str]:
    """调用大模型 ASR 网关转写 16kHz 16-bit 单声道 WAV 音频。
    采用 httpx.AsyncClient(trust_env=False) 绕过本地终端代理。
    返回: (清洗后文本, 原始文本)
    """
    asr_url = f"{settings.main_api_url}/audio/transcriptions"
    headers = {}
    if settings.main_api_key:
        headers["Authorization"] = f"Bearer {settings.main_api_key}"

    try:
        async with httpx.AsyncClient(trust_env=False, timeout=25.0) as client:
            resp = await client.post(
                asr_url,
                headers=headers,
                files={"file": ("chunk.wav", audio_bytes, "audio/wav")},
                data={"model": "ASR"},
            )
            if resp.status_code != 200:
                logger.error("ASR 网关调用失败 HTTP %d: %s", resp.status_code, resp.text)
                return "", resp.text

            raw_text = resp.json().get("text", "")
            cleaned_text = clean_asr_transcript(raw_text)
            return cleaned_text, raw_text
    except Exception as exc:
        logger.exception("ASR 网关请求异常: %s", exc)
        return "", str(exc)
