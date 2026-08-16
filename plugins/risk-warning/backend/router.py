"""业务 REST 挂于平台 app/platform/api（契约 /api/v1/risk-fills 等）；本插件不另挂路由。"""
from fastapi import APIRouter

router = APIRouter()
