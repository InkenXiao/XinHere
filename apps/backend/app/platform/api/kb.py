from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...persistence.models import SysUser
from ...persistence.session import get_db
from ...services import kb as kb_svc
from .deps import current_user

logger = __import__("logging").getLogger(__name__)

router = APIRouter(prefix="/kb", tags=["kb"])


@router.get("/sources")
def sources(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return {"items": kb_svc.list_sources(db)}


class SearchIn(BaseModel):
    query: str
    kb_id: str | None = None


@router.post("/search")
def search(body: SearchIn, user: SysUser = Depends(current_user)):
    try:
        return {"hits": kb_svc.search(body.query, body.kb_id)}
    except Exception as exc:  # 网关不可达等上游异常 → 优雅 502（不冒 500）
        logger.warning("kb/search 上游异常：%s", exc)
        raise errors.upstream("知识库检索服务暂不可用，请稍后再试")
