"""客户端日志记录"""

import logging
import sys


def get_logger() -> logging.Logger:
    logger = logging.getLogger("lanshan-client")
    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(fmt)
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)  # CLI默认只显示警告以上
    return logger
