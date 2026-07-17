"""项目进度追踪 CLI 命令

track <项目名>           - 自然语言搜索，自动遍历所有数据表
track <多维表格URL>      - 指定URL，分析该表格
"""

import click
from cli.app import run_async, print_result
from utils.http_client import http_client
from utils.config import client_config


@click.command(name="track")
@click.argument("input_text")
def track_command(input_text):
    """项目进度追踪，生成分析报告并通过机器人发送

    \b
    示例:
      track 鸡精下乡
      track https://xxx.feishu.cn/base/APP_TOKEN?table=TABLE_ID
    """
    access_token = client_config.get_access_token()

    payload = {
        "input_text": input_text,
        "user_access_token": access_token or "",
    }

    result = run_async(http_client.post("/api/v1/track", payload))
    print_result(result, f"项目进度报告: {input_text}")

    if result.get("ok") and result.get("message_sent"):
        click.echo(f"报告已发送至飞书！消息ID: {result.get('message_id', '')}")
    elif result.get("ok"):
        click.echo("报告生成成功，但发送失败")