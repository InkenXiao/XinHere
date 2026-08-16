from __future__ import annotations


class AppError(Exception):
    """封闭码表业务异常。"""

    def __init__(self, code: str, message: str, status: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def unauthorized(msg: str = "未认证") -> AppError:
    return AppError("UNAUTHORIZED", msg, 401)


def forbidden(msg: str = "无权限") -> AppError:
    return AppError("FORBIDDEN", msg, 403)


def not_found(msg: str = "资源不存在") -> AppError:
    return AppError("NOT_FOUND", msg, 404)


def validation(msg: str, details: dict | None = None) -> AppError:
    return AppError("VALIDATION_ERROR", msg, 400, details)


def run_busy() -> AppError:
    return AppError("RUN_BUSY", "会话已有进行中的任务", 409)


def interrupt_mismatch() -> AppError:
    return AppError("INTERRUPT_MISMATCH", "interrupt_id 与当前待处理中断不匹配", 409)


def session_archived() -> AppError:
    return AppError("SESSION_ARCHIVED", "会话已归档", 409)


def upstream(msg: str = "上游服务不可用") -> AppError:
    return AppError("UPSTREAM_ERROR", msg, 502)
