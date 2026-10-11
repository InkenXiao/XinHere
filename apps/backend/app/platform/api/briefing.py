"""信息简报（驾驶舱「信息简报」卡弹窗数据源）。

出站代理 SCMS ai-session-service 月度简报 SSE 流（老前端 AAAAA-ai-chat 企业简报页
同一接口），用户 accessToken 透传（Authorization Bearer），逐帧直通不落库（简报
按需生成，内容实时流式，无需会话事件化）。
"""
from __future__ import annotations

from fastapi import APIRouter

import json
import logging
from datetime import date

from pydantic import BaseModel, Field, model_validator
from sse_starlette.sse import EventSourceResponse



from ...core import errors
from ..scms import briefing as scms_briefing
from ..scms import token_auth

router = APIRouter(tags=["briefing"])
logger = logging.getLogger(__name__)


class BriefingQuery(BaseModel):
    """月份查询：2020-01 起至当月止（默认当前年月，仅拒绝未来月份）。"""

    year: int = Field(ge=2020)
    month: int = Field(ge=1, le=12)

    @model_validator(mode="after")
    def _not_future(self):
        today = date.today()
        if (self.year, self.month) > (today.year, today.month):
            raise ValueError("月份不能晚于当月")
        return self


@router.post("/briefing/monthly/stream")
def monthly_stream(body: BriefingQuery):
    """月度信息简报 SSE 代理。

    token 必须在路由体内先取再闭包传入：auth_and_ctx 中间件 finally 复位
    contextvars，而 StreamingResponse 生成器在中间件返回后才执行（与 agent
    RunHandle 显式携带同一约定）。上游错误帧格式对齐 event_type 形态，
    前端按 user_message ?? message 兜底展示。
    """
    token = token_auth.current_user_token()
    if not token:
        raise errors.unauthorized("登录已失效，请重新登录")

    def gen():
        try:
            yield from scms_briefing.stream_briefing(body.year, body.month, token)
        except RuntimeError as exc:
            logger.warning("briefing 流异常 year=%s month=%s err=%s", body.year, body.month, exc)
            yield {"data": json.dumps(
                {"event_type": "error", "data": {"message": str(exc)}},
                ensure_ascii=False,
            )}

    return EventSourceResponse(gen())
