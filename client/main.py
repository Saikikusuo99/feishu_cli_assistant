#!/usr/bin/env python3
"""CLI客户端入口

不依赖 Click 的 invoke_without_command 进入交互模式，
而是直接调用 show_welcome() 和 start_interactive_mode()，
彻底解决启动后立即退出的问题。
"""

import sys
import os
import traceback

# Windows 终端 UTF-8 编码修复（解决 Rich emoji 输出 GBK 编码错误）
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 将 client/src 目录加入 Python 路径
_src_dir = os.path.join(os.path.dirname(__file__), "src")
sys.path.insert(0, _src_dir)


def main():
    import asyncio

    # 启动时同步 token
    try:
        asyncio.run(http_client.sync_token_from_server())
    except Exception as e:
        print(f"  Warning: Failed to sync token from server: {e}")

    # 注册所有命令（cli 用于后续命令分发）
    register_all_commands(cli)

    # 直接启动交互模式，不依赖 cli() 的 invoke_without_command
    try:
        show_welcome()
        start_interactive_mode()
    except Exception as e:
        print(f"\n  Fatal error: {type(e).__name__}: {e}")
        traceback.print_exc()
        print("\n  Press Enter to exit...")
        try:
            input()
        except Exception:
            pass


if __name__ == "__main__":
    # 在 main() 执行前完成导入，确保异常也能被捕获
    from utils.config import client_config
    from utils.http_client import http_client
    from cli.app import cli, show_welcome, start_interactive_mode
    from cli.commands import register_all_commands

    main()