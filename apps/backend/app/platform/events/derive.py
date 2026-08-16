from __future__ import annotations

from ...persistence.models import PlatformSessionEvent


def derive_messages(events: list[PlatformSessionEvent]) -> list[dict]:
    """按 seq 折叠 surface 事件为消息历史（审计/回放/compaction 输入）。

    规则（02 §3.4）：component/submit 只投影 summary；inject 折叠为注入摘要；
    compaction/summary replace 删除被引用 seq 节点。
    """
    msgs: list[tuple[int, dict]] = []
    for e in sorted(events, key=lambda r: r.seq):
        d = e.data
        if e.type == "user/message":
            prefix = "[上下文注入] " if d.get("source") == "inject" else ""
            msgs.append((e.seq, {"role": "user", "content": prefix + d["content"]}))
        elif e.type == "assistant/message":
            msgs.append((e.seq, {"role": "assistant", "content": d["content"]}))
        elif e.type == "tool/result":
            msgs.append(
                (e.seq, {"role": "tool", "name": d.get("name", ""), "content": d.get("content", "")})
            )
        elif e.type == "component/submit":
            msgs.append((e.seq, {"role": "user", "content": d.get("summary", "")}))
        elif e.type == "compaction/summary":
            replaced = set(d.get("replaced_seqs", []))
            msgs = [(s, m) for s, m in msgs if s not in replaced]
            msgs.append((e.seq, {"role": "system", "content": d.get("summary", "")}))
    return [m for _, m in msgs]
