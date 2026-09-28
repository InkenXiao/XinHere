"""用户个人模型偏好 API：按通道查看/设置/清除个人模型覆盖（未设置走 .docker.env 默认）"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...persistence.models import SysUser
from ...persistence.session import get_db
from ...services import model_pref as pref_svc
from .deps import current_user

router = APIRouter(prefix="/model-prefs", tags=["model-prefs"])


class PrefIn(BaseModel):
    api_url: str = ""
    api_key: str = ""
    model: str = ""
    max_tokens: int = 0


@router.get("")
def list_prefs(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return pref_svc.effective(db, user.user_id)


@router.put("/{channel}")
def put_pref(channel: str, body: PrefIn, user: SysUser = Depends(current_user),
             db: Session = Depends(get_db)):
    ov = pref_svc.upsert(db, user.user_id, channel, api_url=body.api_url,
                         api_key=body.api_key, model=body.model,
                         max_tokens=body.max_tokens)
    return {"ok": True, "channel": channel, "override": ov}


@router.delete("/{channel}")
def delete_pref(channel: str, user: SysUser = Depends(current_user),
                db: Session = Depends(get_db)):
    pref_svc.remove(db, user.user_id, channel)
    return {"ok": True, "channel": channel}
