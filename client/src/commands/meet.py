"""会议 CLI 命令

meet <自然语言> - AI智能安排会议
"""

import click
from cli.app import run_async, print_result
from utils.http_client import http_client
from utils.config import client_config
from rich.console import Console

console = Console()

QUALITY_LABELS = {
    "lunch": "午饭时段",
    "overtime": "加班时段",
    "weekend": "周末",
}


@click.command(name="meet")
@click.argument("user_input")
def meet_command(user_input):
    """AI智能安排会议（自然语言输入）

    \b
    示例:
      meet "明天下午2点和产品团队评审需求，1小时"
      meet "周五上午10点和张三李四开周会"
    """
    import re

    # 移除 @ 前缀（飞书 @ 提及语法），避免干扰 AI 解析和飞书搜索
    user_input = re.sub(r"@(\S+)", r"\1", user_input)

    access_token = client_config.get_access_token()
    open_id = client_config.get_open_id()

    payload: dict = {"user_input": user_input}
    if open_id:
        payload["user_open_id"] = open_id
    if access_token:
        payload["user_access_token"] = access_token

    result = run_async(http_client.post("/api/v1/meet/schedule", payload))

    # 统一返回确认信息，需要用户确认才创建
    if result.get("needs_confirmation"):
        quality = result.get("time_quality", "ideal")
        date = result.get("date", "")
        start_time = result.get("start_time", "")
        end_time = result.get("end_time", "")
        topic = result.get("topic", "")

        console.print()
        if quality == "ideal":
            console.print(
                f"  [bold green]✅ 找到合适时间：{date} {start_time}-{end_time}，确认创建？[/]"
            )
        else:
            quality_desc = QUALITY_LABELS.get(quality, quality)
            console.print(
                f"  [bold yellow]⚠ 没有特别合适的时间（{quality_desc}），"
                f"{date} {start_time}-{end_time} 是否接受？[/]"
            )

        try:
            confirmed = click.confirm("  确认创建会议？", default=True)
        except Exception:
            confirmed = False

        if confirmed:
            payload["confirm"] = True
            result = run_async(http_client.post("/api/v1/meet/schedule", payload))
            print_result(result, f"智能安排: {user_input}")
        else:
            console.print("  [yellow]已取消会议安排[/]")
            console.print()
    else:
        print_result(result, f"智能安排: {user_input}")
