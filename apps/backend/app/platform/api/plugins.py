from __future__ import annotations

from fastapi import APIRouter, Depends

from ...persistence.models import SysUser
from ..plugins.loader import plugin_manifests
from .deps import current_user

router = APIRouter(tags=["plugins"])


@router.get("/plugins")
def plugins(user: SysUser = Depends(current_user)):
    return plugin_manifests()
