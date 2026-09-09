"""对话页模型通道选项 (标准/快速; 快速仅在配置了与主模型不同的小模型时暴露)"""
from fastapi import APIRouter, Depends

from ...core.config import settings
from .deps import current_user

router = APIRouter(prefix="/models", tags=["models"])


@router.get("")
def list_models(user=Depends(current_user)):
    items = [{"key": "main", "label": "标准", "model": settings.main_model}]
    small = (settings.small_model or "").strip()
    if small and small != settings.main_model:
        items.append({"key": "small", "label": "快速", "model": small})
    return {"items": items}
