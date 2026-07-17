"""CLI应用入口

基于Click框架的CLI应用。
"""

import asyncio
import click
import time
from rich.console import Console
from rich.table import Table

from utils.config import client_config
from utils.http_client import http_client

console = Console()

# 会话级别用户角色（启动时通过 /api/v1/auth/whoami 获取）
_session_role: str = ""
_session_name: str = ""


@click.group()
@click.version_option(version="0.1.0", prog_name="AI Agent 飞书自动化 CLI")
@click.pass_context
def cli(ctx):
    """AI Agent 飞书自动化系统 CLI 客户端"""
    pass  # 交互模式由 main.py 直接启动，cli 仅用于命令分发


def _check_server_available() -> bool:
    """检查服务端是否可用"""
    try:
        result = asyncio.run(http_client.health())
        return result.get("ok", False) or result.get("status") == "ok"
    except Exception:
        return False


def _check_token_valid() -> tuple[bool, str]:
    """检查用户token是否有效

    Returns:
        (是否有效, 状态描述)
    """
    token_info = client_config.get_user_token()
    if not token_info:
        return False, "no token"

    access_token = token_info.get("access_token", "")
    if not access_token:
        return False, "token is empty"

    expires_at = token_info.get("expires_at", 0)
    if time.time() >= expires_at:
        return False, "token expired"

    return True, f"valid (expires: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expires_at))})"


def _fetch_user_role() -> bool:
    """启动时获取当前用户角色（通过服务端双重鉴权）

    Returns:
        True 表示成功获取角色
    """
    global _session_role, _session_name
    try:
        result = asyncio.run(http_client.get("/api/v1/auth/whoami"))
        if result.get("ok"):
            _session_role = result.get("role", "member")
            _session_name = result.get("name", "")
            client_config.user_role = _session_role
            return True
        else:
            error = result.get("error", result.get("detail", "鉴权失败"))
            console.print(f"  [bold red]✗ 身份鉴权失败: {error}[/]")
            return False
    except Exception as e:
        console.print(f"  [bold red]✗ 无法连接服务端进行鉴权: {e}[/]")
        return False


def show_welcome():
    """显示欢迎界面"""
    global _session_role, _session_name
    console.print()
    console.print("=" * 60, style="bold blue")
    console.print("  AI Agent Feishu Automation CLI", style="bold white")
    console.print("  Version: 0.1.0", style="white")
    console.print("=" * 60, style="bold blue")

    console.print(f"\n  Backend API: ", style="white", end="")
    console.print(client_config.server_url, style="bold green")

    console.print(f"\n  Server Status: ", style="white", end="")
    if _check_server_available():
        console.print("✅ Running", style="bold green")
    else:
        console.print("❌ Not Started", style="bold red")
        console.print("\n  [bold yellow]⚠️  Please start the server first, then restart the client CLI[/]")
        console.print("     Run: start_server.bat or python server/main.py")
        console.print("=" * 60, style="bold blue")
        console.print()
        return

    console.print(f"\n  Current User: ", style="white", end="")
    console.print(
        f"{client_config.user_name or 'N/A'} ({client_config.user_role})",
        style="bold green",
    )

    token_valid, token_desc = _check_token_valid()
    console.print(f"\n  Auth Status: ", style="white", end="")
    if token_valid:
        console.print("✅ " + token_desc, style="bold green")
    else:
        console.print("❌ " + token_desc, style="bold red")
        console.print("\n  [bold yellow]⚠️  Token invalid, syncing from server...[/]")

        try:
            sync_success = asyncio.run(http_client.sync_token_from_server())
            if sync_success:
                token_valid, token_desc = _check_token_valid()
                console.print(f"\n  Auth Status: ", style="white", end="")
                console.print("✅ " + token_desc, style="bold green")
            else:
                console.print("\n  [bold red]✗ Sync failed, starting auth flow...[/]")
                from commands.auth import auth_login
                auth_login()
        except Exception as e:
            console.print(f"\n  [bold red]✗ Sync failed: {e}[/]")
            from commands.auth import auth_login
            auth_login()

    # 启动时双重鉴权：获取用户角色
    console.print(f"\n  Identity Check: ", style="white", end="")
    if _fetch_user_role():
        role_icon = "🔑" if _session_role == "admin" else "👤"
        console.print(f"{role_icon} {_session_role.upper()} ({_session_name})", style="bold green")
    else:
        console.print("⚠️  降级为本地角色", style="bold yellow")
        _session_role = client_config.user_role or "member"

    # 根据角色显示命令列表
    is_admin = _session_role == "admin"

    console.print(f"\n[bold cyan]Commands ({'Admin' if is_admin else 'Member'}):[/]")
    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Command", style="bold green")
    table.add_column("Description", style="white")

    # 所有用户可用的命令
    table.add_row("health", "System health check")
    table.add_row("todo <content>", "Create Feishu task")
    table.add_row("meet <desc>", "AI schedule meeting")
    table.add_row("brief <doc_url>", "Generate doc summary")
    table.add_row("auth login", "Login & authorize")
    console.print(table)

    if is_admin:
        console.print(f"\n[bold yellow]Admin-Only Commands:[/]")
        admin_table = Table(show_header=True, header_style="bold magenta")
        admin_table.add_column("Command", style="bold yellow")
        admin_table.add_column("Description", style="white")
        admin_table.add_row("track", "Task tracking & reports")
        admin_table.add_row("cost", "Expense management")
        admin_table.add_row("audit", "Approval management")
        admin_table.add_row("execute <desc>", "AI task execution")
        admin_table.add_row("remind", "Gentle reminders")
        admin_table.add_row("form <project|url>", "Analyze form data")
        admin_table.add_row("crm", "CRM customer data")
        admin_table.add_row("admin users", "Manage user roles")
        admin_table.add_row("admin set-role", "Set user role")
        console.print(admin_table)

    console.print("\nType", style="white", end="")
    console.print(" help", style="bold green", end="")
    console.print(" for more options", style="white")
    console.print("=" * 60, style="bold blue")
    console.print()


def print_result(result: dict, title: str = "执行结果"):
    """美化输出执行结果"""
    console.print()
    console.print("=" * 60, style="bold blue")
    console.print(f"  {title}", style="bold white")
    console.print("=" * 60, style="bold blue")

    if not result:
        console.print("  无返回结果", style="yellow")
        return

    if result.get("ok") is False or result.get("status") == "error":
        console.print(f"  [bold red][X][/] {result.get('error', result.get('message', 'unknown error'))}")
    else:
        status = result.get("status", "ok")
        icon = "[OK]" if status == "ok" else "[!!]"
        style = "bold green" if status == "ok" else "bold yellow"

        console.print(f"  [{style}]{icon} Status: {status}")

        if "message" in result:
            console.print(f"     信息: {result['message']}", style="white")

        if result.get("overall"):
            console.print(f"     综合状态: {result['overall']}", style="bold green" if result["overall"] == "ok" else "bold red")

        if result.get("summary"):
            summary = result["summary"]
            console.print("\n  📈 执行摘要", style="bold cyan")
            console.print("  " + "-" * 50)
            if isinstance(summary, dict):
                console.print(f"  总任务数: {summary.get('total_tasks', 0)}")
                console.print(f"  已完成:   {summary.get('completed_tasks', 0)}")
                console.print(f"  进行中:   {summary.get('in_progress_tasks', 0)}")
                console.print(f"  延迟:     {summary.get('delayed_tasks', 0)}")
                console.print(f"  完成率:   {summary.get('completion_rate', 0)}%")
                console.print(f"  延迟率:   {summary.get('delay_rate', 0)}%")
            else:
                # 字符串类型的摘要（如 brief 命令）
                console.print(f"  {summary}", style="white")

        if result.get("message_sent"):
            console.print(f"\n  [bold green]已发送文档到你的飞书[/]")

        if result.get("report_text"):
            console.print("\n  📝 报告内容", style="bold cyan")
            console.print("  " + "-" * 50)
            console.print(f"  {result['report_text']}", style="white")

        if result.get("risk_items"):
            console.print("\n  🚨 风险预警", style="bold cyan")
            console.print("  " + "-" * 50)
            for i, risk in enumerate(result["risk_items"]):
                console.print(f"  {i+1}. {risk}", style="bold yellow")

        if result.get("next_week_suggestions"):
            console.print("\n  💡 下周建议", style="bold cyan")
            console.print("  " + "-" * 50)
            for i, suggestion in enumerate(result["next_week_suggestions"]):
                console.print(f"  {i+1}. {suggestion}", style="green")

    console.print("\n" + "=" * 60, style="bold blue")
    console.print()


def run_async(coro):
    """运行异步协程"""
    return asyncio.run(coro)


def start_interactive_mode():
    """启动交互式命令模式"""
    import shlex
    import sys

    # 管理员命令列表
    ADMIN_COMMANDS = {"track", "cost", "audit", "execute", "remind", "form", "crm", "admin"}

    try:
        while True:
            try:
                cmd_input = input("\nai-agent> ").strip()
                if not cmd_input:
                    continue

                if cmd_input.lower() in ["exit", "quit", "q"]:
                    print("  Goodbye!")
                    break

                if cmd_input.lower() == "help":
                    show_welcome()
                    continue

                if cmd_input.lower() == "clear":
                    console.clear()
                    continue

                # 检查管理员命令权限
                cmd_name = cmd_input.split()[0].lower() if cmd_input else ""
                if cmd_name in ADMIN_COMMANDS and _session_role != "admin":
                    console.print(f"  [bold red]✗ '{cmd_name}' 需要 Admin 权限，当前角色: {_session_role}[/]")
                    continue

                try:
                    args = shlex.split(cmd_input)
                    cli.main(args=args, standalone_mode=False)
                except SystemExit:
                    pass
                except click.ClickException as e:
                    print(f"  Error: {e}")

            except EOFError:
                print("\n  Goodbye!")
                break
            except KeyboardInterrupt:
                print("\n  Goodbye!")
                break
            except Exception as e:
                print(f"\n  Input error: {type(e).__name__}: {e}")
                print("  Continuing...")
    except Exception as e:
        import traceback
        print(f"\n  Fatal error in interactive mode: {type(e).__name__}: {e}")
        print(f"  Traceback:\n{traceback.format_exc()}")
        print("\n  Press Enter to exit...")
        try:
            input()
        except:
            pass
