"""服务端入口

启动FastAPI服务、数据库初始化和飞书长连接客户端。
"""

import sys
import os
import socket
import subprocess
import platform
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.src.core.config import server_config
from server.src.core.logging import setup_logging


def _check_port_available(port: int) -> bool:
    """检查端口是否可用"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(('localhost', port))
            return result != 0
    except Exception:
        return False


def _get_pid_by_port(port: int) -> int | None:
    """获取占用端口的进程PID"""
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in result.stdout.split('\n'):
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.strip().split()
                    if parts:
                        return int(parts[-1])
        else:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.stdout.strip():
                return int(result.stdout.strip())
    except Exception:
        pass
    return None


def _kill_process(pid: int) -> bool:
    """终止进程"""
    try:
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
            )
        else:
            subprocess.run(
                ["kill", "-9", str(pid)],
                capture_output=True,
                timeout=10,
            )
        return True
    except Exception:
        return False


def main():
    logger = setup_logging()
    logger.info(f"启动 AI Agent 飞书自动化系统 服务端 v1.0.0")
    logger.info(f"监听地址: {server_config.host}:{server_config.port}")

    # 检查端口占用
    if not _check_port_available(server_config.port):
        pid = _get_pid_by_port(server_config.port)
        if pid:
            logger.warning(f"端口 {server_config.port} 已被占用 (PID: {pid})，正在自动释放...")
            if _kill_process(pid):
                logger.info(f"进程 {pid} 已终止")
                import time
                time.sleep(1)
            else:
                logger.error(f"无法终止进程 {pid}")
        else:
            logger.warning(f"端口 {server_config.port} 已被占用，但无法获取进程PID")

    import threading
    lc_result = [False]
    lc_error = [None]
    
    def _start_lc():
        try:
            from server.src.feishu.long_connection import start_long_connection
            lc_result[0] = start_long_connection()
        except Exception as e:
            lc_error[0] = e
    
    lc_thread = threading.Thread(target=_start_lc, daemon=True)
    lc_thread.start()
    lc_thread.join(timeout=10)
    
    if lc_thread.is_alive():
        logger.warning("飞书长连接客户端启动超时（10秒），跳过长连接启动")
    elif lc_error[0]:
        logger.error(f"飞书长连接客户端启动异常: {lc_error[0]}")
    elif lc_result[0]:
        logger.info("飞书长连接客户端启动成功")
    else:
        logger.warning("飞书长连接客户端启动失败")

    # 初始化数据库表结构
    import asyncio
    from server.src.db.session import init_db
    asyncio.run(init_db())
    logger.info("数据库表结构初始化完成")

    import pathlib
    server_dir = pathlib.Path(__file__).parent

    uvicorn.run(
        "server.src.app:create_app",
        host=server_config.host,
        port=server_config.port,
        reload=server_config.debug,
        reload_dirs=[str(server_dir)],
        factory=True,
        log_level=server_config.log_level.lower(),
    )


if __name__ == "__main__":
    main()
