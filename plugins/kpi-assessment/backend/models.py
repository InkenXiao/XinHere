"""插件业务表（定义于平台 models，保持单一迁移源）。"""
from app.persistence.models import (  # noqa: F401
    KpiBatch,
    KpiIndicator,
    KpiLampAdjustment,
    KpiMilestone,
    KpiMsFeedback,
)
