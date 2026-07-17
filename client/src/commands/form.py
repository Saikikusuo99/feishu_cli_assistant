"""表单分析 CLI 命令

form <项目名>            - 通过项目名模糊搜索并分析
form <飞书多维表格URL>    - 精确解析URL并分析
"""

import click
from cli.app import run_async, print_result
from utils.http_client import http_client
from utils.config import client_config


@click.command(name="form")
@click.argument("target")
def form_command(target):
    """分析表单数据

    \b
    示例:
      form "6月线下数据统计"
      form https://xxx.feishu.cn/base/appXXX?table=tblYYY
    """
    access_token = client_config.get_access_token()
    user_open_id = client_config.get_open_id()

    payload = {"target": target}
    if access_token:
        payload["user_access_token"] = access_token
    if user_open_id:
        payload["send_to_user"] = user_open_id

    result = run_async(http_client.post("/api/v1/form/analyze", payload))
    print_result(result, f"表单分析: {target}")
