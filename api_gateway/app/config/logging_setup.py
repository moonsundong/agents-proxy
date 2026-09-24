"""Loguru 日志配置:终端只留警告/错误(防刷屏),全量写轮转文件 logs/gateway.log。

uvicorn / sqlalchemy / httpx 等第三方库走标准库 logging,用 InterceptHandler
统一接管进 loguru,所有日志落同一个文件、同一套格式。
"""

import logging
import os
import sys
from pathlib import Path

from loguru import logger

from app.config.settings import settings

_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> <level>{level: <8}</level> "
    "<cyan>{name}:{function}:{line}</cyan> {message}"
)

# Windows 下文件名大小写可能不一致(C:\ vs c:\),统一 normcase 再比
_LOGGING_FILE = os.path.normcase(logging.__file__)
_THIS_FILE = os.path.normcase(__file__)


class _InterceptHandler(logging.Handler):
    """把标准库 logging 的记录转发给 loguru(depth 回溯到真实调用点)。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        # 从 emit 逐帧向上,跳过 logging 模块和本文件,第一个外部帧才是真实调用点。
        # (loguru 文档的经典配方依赖 logging.currentframe() 的实现细节,实测不可靠)
        frame, depth = sys._getframe(), 0
        while frame and os.path.normcase(frame.f_code.co_filename) in (
            _LOGGING_FILE,
            _THIS_FILE,
        ):
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging() -> None:
    logger.remove()
    # 控制台:WARNING 以上,彩色
    logger.add(sys.stderr, level=logging.WARNING, format=_FORMAT)
    # 文件:全量,5MB 轮转,保留 3 份
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logger.add(
        log_dir / "gateway.log",
        level=settings.log_level,
        format=_FORMAT,
        rotation="5 MB",
        retention=3,
        encoding="utf-8",
    )

    # 接管标准库 logging:uvicorn/sqlalchemy 等的记录统一进 loguru
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    # uvicorn 自带的 handler 会重复输出,清掉只留传播;访问日志与
    # RequestLoggingMiddleware 重复,只留警告以上
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers = []
        uv.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # SQLAlchemy 的 SQL 回显太吵,除非 debug 否则只留警告以上
    if not settings.debug:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
