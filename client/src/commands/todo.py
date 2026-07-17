"""待办任务 CLI 命令

/todo <内容> - AI解析并创建飞书任务
"""

import click
from cli.app import run_async, print_result
from utils.http_client import http_client
from utils.config import client_config


@click.command(name="todo")
@click.argument("content")
def todo_command(content):
    """创建飞书任务（支持自然语言输入）

    \b
    示例:
      todo "完成周报 @张三 明天"
      todo "需求评审会议 下周三下午"
      todo "统计d组鲜花打卡 @绿大萌 7月9号下午5点截止，同步老乡鸡食堂预备役群"
    """
    access_token = client_config.get_access_token()
    open_id = client_config.get_open_id()

    payload = {
        "content": content,
        "user_open_id": open_id,
    }
    if access_token:
        payload["user_access_token"] = access_token

    result = run_async(http_client.post("/api/v1/todo", payload))
    print_result(result, f"创建任务: {content}")