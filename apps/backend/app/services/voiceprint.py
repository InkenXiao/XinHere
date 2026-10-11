"""声纹识别服务：CAM++ ONNX（进程内 CPU）说话人识别 + 无感采样累积 + 会议内编号聚类。

链路设计（见 .trae/documents/voiceprint-speaker-recognition.md）：
- fail-soft：模型缺失/加载失败/推理异常一律静默降级，绝不阻塞 ASR 主流程；
- 无感采样：听写/会议分片音频提取 embedding 累积（仅存向量不存音频），达标自动注册；
- 会议未注册说话人：本场在线聚类编号「说话人N」，同人同号，仅本场有效不回溯。
"""
from __future__ import annotations

import io
import logging
import math
import threading
import wave
from typing import Any

import numpy as np
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..core.config import settings
from ..persistence.models import MeetingSpeakerAlias, UserBiometricConsent, UserVoiceprint
from ..persistence.session import new_session
from .identity import user_names

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000  # 网关约定：16kHz 16-bit 单声道 WAV
_FEATURE_DIM = 80  # CAM++ 输入特征维度（fbank）
_MIN_FRAMES = 25  # 最少特征帧（约 0.25s），更短视为无效音频
_MAX_FRAMES = 3000  # 最长约 30s，超出取中间段（内存与一致性保护）
_MIN_ENROLL_CLIPS = 2  # 注册至少 2 条不同时段样本，质心更稳
_ENROLL_WAIT = {"ok": False, "enrolled": False, "just_enrolled": False, "sample_seconds": 0.0}


# ---------------- 引擎（懒加载单例） ----------------


class _Engine:
    """ONNX 引擎懒加载单例：首次使用时加载，失败永久降级关闭。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: Any = None
        self._input_name = ""
        self._failed = False

    @property
    def available(self) -> bool:
        if not settings.voiceprint_enabled or self._failed:
            return False
        if self._session is None:
            self._load()
        return self._session is not None

    def _load(self) -> None:
        with self._lock:
            if self._session is not None or self._failed:
                return
            try:
                import onnxruntime as ort

                so = ort.SessionOptions()
                so.intra_op_num_threads = 1
                so.inter_op_num_threads = 1
                self._session = ort.InferenceSession(
                    settings.voiceprint_model_path, so, providers=["CPUExecutionProvider"],
                )
                self._input_name = self._session.get_inputs()[0].name
                logger.info("声纹引擎加载成功: %s", settings.voiceprint_model_path)
            except Exception:
                self._failed = True
                logger.warning(
                    "声纹引擎加载失败，声纹能力降级关闭（模型: %s，不影响 ASR）",
                    settings.voiceprint_model_path, exc_info=True,
                )

    def run(self, feats: np.ndarray) -> np.ndarray | None:
        """CAM++ 前向：feats [T, 80] → 192 维原始向量。"""
        out = self._session.run(None, {self._input_name: feats[np.newaxis, :, :]})
        return np.asarray(out[0]).reshape(-1).astype(np.float32)


_engine = _Engine()


# ---------------- 音频解码与特征 ----------------


def _decode_wav(raw: bytes) -> np.ndarray | None:
    """PCM WAV → float32 单声道 [-1,1]，重采样到 16kHz；异常/空音频返回 None。"""
    try:
        with wave.open(io.BytesIO(raw), "rb") as w:
            sr = w.getframerate()
            ch = w.getnchannels()
            sw = w.getsampwidth()
            frames = w.readframes(w.getnframes())
        if not frames:
            return None
        if sw == 2:
            x = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        elif sw == 4:
            x = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
        elif sw == 1:
            x = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif sw == 3:
            b = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
            ints = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
            ints = np.where(ints >= 1 << 23, ints - (1 << 24), ints)
            x = ints.astype(np.float32) / float(1 << 23)
        else:
            return None
        if ch > 1:
            x = x.reshape(-1, ch).mean(axis=1)
        if sr != _SAMPLE_RATE and x.size:
            n_out = int(round(x.size * _SAMPLE_RATE / sr))
            if n_out <= 0:
                return None
            src = np.linspace(0.0, x.size - 1, num=x.size)
            dst = np.linspace(0.0, x.size - 1, num=n_out)
            x = np.interp(dst, src, x).astype(np.float32)
        return x if x.size else None
    except Exception:
        logger.debug("WAV 解码失败（%d bytes）", len(raw), exc_info=True)
        return None


def _fbank_features(pcm: np.ndarray) -> np.ndarray | None:
    """80 维 fbank 特征（kaldi-native-fbank，与 CAM++ ONNX 部署同款前端）；帧数不足返回 None。"""
    import kaldi_native_fbank as knf

    opts = knf.FbankOptions()
    opts.frame_opts.samp_freq = _SAMPLE_RATE
    opts.frame_opts.dither = 0
    opts.mel_opts.num_bins = _FEATURE_DIM
    fb = knf.OnlineFbank(opts)
    fb.accept_waveform(_SAMPLE_RATE, pcm.tolist())
    fb.input_finished()
    n = fb.num_frames_ready  # int 属性（非方法），输入结束后即为总帧数
    if n < _MIN_FRAMES:
        return None
    if n > _MAX_FRAMES:  # 超长取中间段
        start = (n - _MAX_FRAMES) // 2
        frames = [fb.get_frame(i) for i in range(start, start + _MAX_FRAMES)]
    else:
        frames = [fb.get_frame(i) for i in range(n)]
    return np.stack(frames).astype(np.float32)


def _embed(wav_bytes: bytes) -> tuple[np.ndarray, float] | None:
    """音频 bytes → (L2 归一化向量, 秒数)；引擎不可用/解码失败/推理异常返回 None。"""
    if not _engine.available:
        return None
    pcm = _decode_wav(wav_bytes)
    if pcm is None:
        return None
    feats = _fbank_features(pcm)
    if feats is None:
        return None
    try:
        vec = _engine.run(feats)
    except Exception:
        logger.warning("声纹推理异常，本段跳过（不影响 ASR）", exc_info=True)
        return None
    if vec is None:
        return None
    norm = float(np.linalg.norm(vec))
    if norm <= 0 or not math.isfinite(norm):
        return None
    return vec / norm, pcm.size / _SAMPLE_RATE


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na <= 0 or nb <= 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# ---------------- 同意存证（PIPL 单独同意） ----------------


def consent_record(user_id: str) -> UserBiometricConsent | None:
    """同意存证行（detach 后只读返回）；查询异常视为未同意。"""
    if not user_id:
        return None
    try:
        with new_session() as db:
            row = db.query(UserBiometricConsent).filter(
                UserBiometricConsent.user_id == user_id,
                UserBiometricConsent.consent_type == "voiceprint",
            ).first()
            if row is not None:
                db.expunge(row)
            return row
    except Exception:
        logger.warning("同意存证查询失败 uid=%s（视为未同意）", user_id, exc_info=True)
        return None


def has_consented(user_id: str) -> bool:
    return consent_record(user_id) is not None


def record_consent(user_id: str, version: str) -> None:
    """同意存证 upsert：一人一类型一条，重复同意覆盖版本与时间。"""
    with new_session() as db:
        row = db.query(UserBiometricConsent).filter(
            UserBiometricConsent.user_id == user_id,
            UserBiometricConsent.consent_type == "voiceprint",
        ).first()
        if row is None:
            db.add(UserBiometricConsent(
                user_id=user_id, consent_type="voiceprint", protocol_version=version,
            ))
        else:
            row.protocol_version = version
            row.agreed_at = func.now()
        db.commit()


# ---------------- 无感采样累积 ----------------


def accumulate_sample(user_id: str, wav_bytes: bytes, seconds: float | None = None) -> dict:
    """无感采样：提取向量累积进 user_voiceprints，达标（≥min 秒且≥2 条）自动质心注册。

    仅在用户已同意（voiceprint 类型存证）后生效；返回 {"ok", "enrolled", "just_enrolled",
    "sample_seconds", "consented"}（consented=false 仅出现在未同意分支，供前端区分
    「未同意不采样」与「已同意但本段未采上」）；任何失败不抛异常。
    """
    if not user_id or not has_consented(user_id):
        return dict(_ENROLL_WAIT, consented=False)
    emb = _embed(wav_bytes)
    if emb is None:
        return dict(_ENROLL_WAIT)
    vec, inferred = emb
    if not seconds or seconds <= 0:
        seconds = inferred
    return _accumulate_vec(user_id, vec, seconds)


def seed_profile_from_alias(user_id: str, embedding: list[float] | None, sample_seconds: float) -> bool:
    """用会议编号质心（远场真人语音）直接重建注册档案。

    解决信道失配：听写无感采样是近讲麦克风，与会议远场拾音的余弦相似度实测仅 0.33~0.40，
    逐分片降阈值无解（与他人分差 < margin）。用户「这是我」绑定时，用该编号累积的干净
    远场质心整体覆盖注册档案。要求：已同意、质心有效、语音量 ≥5s；失败不抛异常。
    """
    if not user_id or not has_consented(user_id):
        return False
    try:
        vec = np.asarray(embedding or [], dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        if vec.size < 128 or norm <= 0 or sample_seconds < 5.0:
            return False
        with new_session() as db:
            with db.begin():
                row = db.query(UserVoiceprint).filter(
                    UserVoiceprint.user_id == user_id,
                ).with_for_update().first()
                if row is None:
                    row = UserVoiceprint(user_id=user_id)
                    db.add(row)
                    db.flush()
                row.embedding = (vec / norm).tolist()
                row.dimension = int(vec.size)
                row.sample_seconds = float(sample_seconds)
                row.samples = []
                row.enrolled = True
        logger.info(
            "声纹档案已用会议远场质心重建 uid=%s seconds=%.1f", user_id, sample_seconds
        )
        return True
    except Exception:
        logger.warning("声纹档案远场重建失败 uid=%s（不影响改名主流程）", user_id, exc_info=True)
        return False


def _accumulate_vec(user_id: str, vec: np.ndarray, seconds: float) -> dict:
    """采样累积 DB 事务（行锁保护，听写与会议分段并发安全）。"""
    result = dict(_ENROLL_WAIT)
    if not user_id or seconds <= 0:
        return result
    min_sec = float(settings.voiceprint_min_enroll_seconds)
    max_samples = int(settings.voiceprint_max_samples)
    try:
        with new_session() as db:
            row = db.query(UserVoiceprint).filter(
                UserVoiceprint.user_id == user_id,
            ).with_for_update().first()
            if row is None:
                row = UserVoiceprint(user_id=user_id)
                db.add(row)
                db.flush()
            result["enrolled"] = bool(row.enrolled)
            if row.enrolled:  # 已注册不再采样
                result["ok"] = True
                result["sample_seconds"] = float(row.sample_seconds or 0.0)
                return result
            samples = list(row.samples or [])
            samples.append({"vec": vec.tolist(), "seconds": round(seconds, 2)})
            while len(samples) > max_samples:  # 超限合并最早两条（秒数加权），保留累积信息
                a, b = samples[0], samples[1]
                total = float(a.get("seconds", 0)) + float(b.get("seconds", 0))
                merged = (
                    np.asarray(a["vec"], dtype=np.float32) * float(a.get("seconds", 0))
                    + np.asarray(b["vec"], dtype=np.float32) * float(b.get("seconds", 0))
                ) / max(total, 1e-6)
                samples = [{"vec": merged.tolist(), "seconds": round(total, 2)}] + samples[2:]
            total_seconds = float(row.sample_seconds or 0.0) + seconds
            row.samples = samples
            row.sample_seconds = total_seconds
            result["sample_seconds"] = total_seconds
            if total_seconds >= min_sec and len(samples) >= _MIN_ENROLL_CLIPS:
                wsum = sum(max(float(s.get("seconds", 0)), 1e-6) for s in samples)
                centroid = np.zeros(vec.size, dtype=np.float32)
                for s in samples:
                    centroid += np.asarray(s["vec"], dtype=np.float32) * (float(s.get("seconds", 0)) / wsum)
                norm = float(np.linalg.norm(centroid))
                if norm > 0:
                    row.embedding = (centroid / norm).tolist()
                    row.dimension = int(centroid.size)
                    row.enrolled = True
                    row.samples = []  # 质心已入 embedding，清空缓存
                    result["enrolled"] = True
                    result["just_enrolled"] = True
            db.commit()
            result["ok"] = True
            return result
    except Exception:
        logger.warning("声纹采样累积失败 uid=%s（不影响主流程）", user_id, exc_info=True)
        return result


# ---------------- 注册声纹比对 ----------------


def identify_registered(embedding: np.ndarray) -> tuple[str, str] | None:
    """全量比对已注册声纹：top1 ≥ 阈值且领先 top2 ≥ 间隔 → (user_id, 显示名)。

    top1-top2 间隔判定防「两人声纹接近时张冠李戴」：两人分数咬得紧说明判别力不足，
    判定为歧义不实名，回落会议编号聚类。debug 日志输出分数分布供阈值校准取数。
    """
    if embedding is None:
        return None
    thr = float(settings.voiceprint_match_threshold)
    margin = float(settings.voiceprint_match_margin)
    try:
        with new_session() as db:
            rows = db.query(UserVoiceprint).filter(UserVoiceprint.enrolled.is_(True)).all()
        top1: tuple[str, float] | None = None
        top2_score = -1.0
        for r in rows:
            if not r.embedding:
                continue
            score = cosine_similarity(embedding, np.asarray(r.embedding, dtype=np.float32))
            if top1 is None or score > top1[1]:
                if top1 is not None:
                    top2_score = top1[1]
                top1 = (r.user_id, score)
            elif score > top2_score:
                top2_score = score
        if top1 is None:
            return None
        logger.debug(
            "声纹比对 uid=%s top1=%.3f top2=%.3f（thr=%.2f margin=%.2f）",
            top1[0], top1[1], top2_score, thr, margin,
        )
        if top1[1] < thr or (top2_score >= 0 and top1[1] - top2_score < margin):
            return None
        names = user_names([top1[0]])
        return top1[0], names.get(top1[0]) or top1[0]
    except Exception:
        logger.warning("注册声纹比对失败（视为未命中）", exc_info=True)
        return None


# ---------------- 会议内编号聚类（本场有效，不回溯） ----------------


def match_or_create_alias(meeting_id: str, embedding: np.ndarray, seconds: float = 0.0) -> dict | None:
    """本场在线聚类：命中已有编号（≥归并阈值）→ 复用并加权更新质心；否则新建「说话人N」。

    归并阈值独立低于实名阈值（远场相似度波动大，宁归并不新建，减少说话人碎片化）。
    命中的编号若已绑定实名（resolved_user_id），一并返回由调用方直接显示实名。
    返回 {"label": str, "resolved_user_id": str|None}；失败返回 None（调用方回落「员工」）。
    """
    if not meeting_id or embedding is None:
        return None
    thr = float(settings.voiceprint_alias_match_threshold)
    try:
        with new_session() as db:
            rows = db.query(MeetingSpeakerAlias).filter(
                MeetingSpeakerAlias.meeting_id == meeting_id,
            ).with_for_update().all()
            best: MeetingSpeakerAlias | None = None
            best_score = thr
            for r in rows:
                if not r.embedding:
                    continue
                score = cosine_similarity(embedding, np.asarray(r.embedding, dtype=np.float32))
                if score >= best_score:
                    best, best_score = r, score
            if best is not None:
                # 短碎片段只复用编号不更新质心：短音频 embedding 方差大，直接合并会拉偏聚类质心
                if seconds >= float(settings.voiceprint_alias_min_update_seconds):
                    old = np.asarray(best.embedding, dtype=np.float32)
                    old_w = max(float(best.sample_seconds or 0.0), 1e-6)
                    seg_w = max(float(seconds or 0.0), 1e-6)
                    merged = (old * old_w + embedding * seg_w) / (old_w + seg_w)
                    norm = float(np.linalg.norm(merged))
                    if norm > 0:
                        best.embedding = (merged / norm).tolist()
                        best.sample_seconds = old_w + seg_w
                    db.commit()
                return {"label": best.speaker_label, "resolved_user_id": best.resolved_user_id}
            label = f"说话人{len(rows) + 1}"
            db.add(MeetingSpeakerAlias(
                meeting_id=meeting_id, speaker_label=label,
                embedding=embedding.tolist(), sample_seconds=float(seconds or 0.0),
            ))
            try:
                db.commit()
            except IntegrityError:
                # 并发建号撞唯一约束（meeting_id + speaker_label）：回落已有编号行
                db.rollback()
                dup = db.query(MeetingSpeakerAlias).filter(
                    MeetingSpeakerAlias.meeting_id == meeting_id,
                    MeetingSpeakerAlias.speaker_label == label,
                ).first()
                if dup is not None:
                    return {"label": dup.speaker_label, "resolved_user_id": dup.resolved_user_id}
                raise
            return {"label": label, "resolved_user_id": None}
    except Exception:
        logger.warning("会议说话人编号失败 meeting=%s（视为未编号）", meeting_id, exc_info=True)
        return None


def _resolve_meeting_alias_labels(meeting_id: str, user_id: str, embedding: np.ndarray) -> list[str]:
    """注册成功后绑定本场编号（fail-soft）：与实名声纹相似度 ≥ 实名阈值的未绑定编号写入 user_id。

    仅负责 alias 绑定并返回命中 label 列表；历史字幕回填/转写稿重写/speakers 同步/
    内存态刷新/SSE 广播由 modules 层（meeting.service.backfill_resolved_speaker）完成，
    services 层不反向依赖 modules。
    """
    thr = float(settings.voiceprint_match_threshold)
    labels: list[str] = []
    try:
        with new_session() as db:
            rows = db.query(MeetingSpeakerAlias).filter(
                MeetingSpeakerAlias.meeting_id == meeting_id,
                MeetingSpeakerAlias.resolved_user_id.is_(None),
            ).all()
            for r in rows:
                if r.embedding and cosine_similarity(
                    embedding, np.asarray(r.embedding, dtype=np.float32),
                ) >= thr:
                    r.resolved_user_id = user_id
                    labels.append(r.speaker_label)
            if labels:
                db.commit()
    except Exception:
        logger.warning("会议编号实名绑定失败 meeting=%s uid=%s（不影响主流程）", meeting_id, user_id, exc_info=True)
        return []
    return labels


# ---------------- 会议分片聚合入口 ----------------


def process_meeting_audio(meeting_id: str, uploader_id: str, wav_bytes: bytes) -> dict:
    """会议分片段声纹链路（同步 CPU/DB，调用方建议 asyncio.to_thread 包装）。

    已同意前提下：识别注册声纹 → 命中返回实名；未命中 → 本场编号聚类
    （编号已绑定实名则直接显示实名），同时静默累积进上传者采样，
    达标注册成功后立即绑定本场相似编号，返回 just_resolved_labels 由
    调用方（modules 层）批量回填历史字幕并广播 SSE。
    返回 {"speaker_id": str|None, "speaker_name": str|None, "just_resolved_labels": list[str]}；
    speaker 为 None 由调用方回落「员工」。
    """
    empty = {"speaker_id": None, "speaker_name": None}
    if not meeting_id or not wav_bytes:
        return empty
    try:
        if not has_consented(uploader_id):
            return empty
        emb = _embed(wav_bytes)
        if emb is None:
            return empty
        vec, seconds = emb
        # 短分片 embedding 不稳定，是实名误报的主源：<2s 只走本场编号聚类，不参与实名比对
        if seconds >= float(settings.voiceprint_min_identify_seconds):
            hit = identify_registered(vec)
            if hit is not None:
                return {"speaker_id": hit[0], "speaker_name": hit[1]}
        alias = match_or_create_alias(meeting_id, vec, seconds)
        resolved = (alias or {}).get("resolved_user_id")
        if resolved:
            names = user_names([resolved])
            return {"speaker_id": resolved, "speaker_name": names.get(resolved) or resolved}
        label = (alias or {}).get("label")
        acc = _accumulate_vec(uploader_id, vec, seconds)
        if acc.get("just_enrolled"):
            labels = _resolve_meeting_alias_labels(meeting_id, uploader_id, vec)
            if label in labels:
                # 当前分片所在编号刚绑定实名：直接显示实名，不再返回编号
                name = user_names([uploader_id]).get(uploader_id) or uploader_id
                return {"speaker_id": uploader_id, "speaker_name": name, "just_resolved_labels": labels}
            return {"speaker_id": None, "speaker_name": label, "just_resolved_labels": labels}
        return {"speaker_id": None, "speaker_name": label}
    except Exception:
        logger.warning("会议声纹链路异常 meeting=%s（回落员工）", meeting_id, exc_info=True)
        return empty


# ---------------- 采样进度（router 用） ----------------


def delete_voiceprint(user_id: str) -> dict:
    """删除当前用户全部声纹数据（采样与注册档案，PIPL 删除权）。

    删除后：identify_registered 不再命中该用户；无感采样需重新同意并重新累积。
    同时解绑全部会议编号实名引用，历史编号回落「说话人N」。
    返回 {"deleted": bool}；异常上抛由 API 层返回错误。
    """
    if not user_id:
        return {"deleted": False}
    with new_session() as db:
        rows = db.query(UserVoiceprint).filter(UserVoiceprint.user_id == user_id).delete(synchronize_session=False)
        # 解绑会议编号实名引用：撤回/删除后已绑定编号不得再返回实名
        db.query(MeetingSpeakerAlias).filter(
            MeetingSpeakerAlias.resolved_user_id == user_id,
        ).update({MeetingSpeakerAlias.resolved_user_id: None}, synchronize_session=False)
        db.commit()
    if rows:
        logger.info("声纹数据已删除 uid=%s rows=%s", user_id, rows)
    return {"deleted": rows > 0}


def revoke_consent(user_id: str) -> dict:
    """撤回生物信息采集同意并连带删除声纹数据（PIPL 撤回权）。

    撤回后 has_consented=False，无感采样立即停止；存证行删除（审计由访问日志承担）。
    同时解绑全部会议编号实名引用，已绑定编号回落「说话人N」。
    """
    if not user_id:
        return {"revoked": False, "deleted": False}
    with new_session() as db:
        consent_rows = db.query(UserBiometricConsent).filter(
            UserBiometricConsent.user_id == user_id,
            UserBiometricConsent.consent_type == "voiceprint",
        ).delete(synchronize_session=False)
        vp_rows = db.query(UserVoiceprint).filter(
            UserVoiceprint.user_id == user_id,
        ).delete(synchronize_session=False)
        # 解绑会议编号实名引用：撤回后不得再经编号路径返回实名
        db.query(MeetingSpeakerAlias).filter(
            MeetingSpeakerAlias.resolved_user_id == user_id,
        ).update({MeetingSpeakerAlias.resolved_user_id: None}, synchronize_session=False)
        db.commit()
    if consent_rows or vp_rows:
        logger.info(
            "生物信息采集同意已撤回并删除声纹数据：uid=%s consent_rows=%s vp_rows=%s",
            user_id, consent_rows, vp_rows,
        )
    return {"revoked": consent_rows > 0, "deleted": vp_rows > 0}


def status(user_id: str) -> dict:
    """采样进度：{"enrolled", "sample_seconds", "min_seconds"}；异常时返回未注册默认。"""
    out = {
        "enrolled": False,
        "sample_seconds": 0.0,
        "min_seconds": float(settings.voiceprint_min_enroll_seconds),
    }
    if not user_id:
        return out
    try:
        with new_session() as db:
            row = db.query(UserVoiceprint).filter(UserVoiceprint.user_id == user_id).first()
        if row is not None:
            out["enrolled"] = bool(row.enrolled)
            out["sample_seconds"] = float(row.sample_seconds or 0.0)
    except Exception:
        logger.warning("声纹状态查询失败 uid=%s", user_id, exc_info=True)
    return out
