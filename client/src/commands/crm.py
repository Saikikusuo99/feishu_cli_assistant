"""CRM查询 CLI 命令

/crm query <客户名> - 查询客户信息（Admin权限）
/crm list - 获取客户列表（Admin权限）
"""

import click
from cli.app import run_async, print_result
from utils.http_client import http_client
from utils.config import client_config


@click.group(name="crm")
def crm_group():
    """CRM查询（Admin权限）"""
    pass


@crm_group.command(name="query")
@click.argument("customer_name")
@click.option("--chat-id", help="群聊ID（推送信息）")
def crm_query(customer_name, chat_id):
    """查询客户信息

    \b
    示例:
      crm query "北京科技有限公司"
      crm query "上海贸易集团" --chat-id oc_xxx
    """
    access_token = client_config.get_access_token()

    payload = {"customer_name": customer_name}
    if chat_id:
        payload["chat_id"] = chat_id
    if access_token:
        payload["user_access_token"] = access_token

    result = run_async(http_client.post("/api/v1/crm", payload))
    print_result(result, f"客户查询: {customer_name}")


@crm_group.command(name="list")
@click.option("--status", "-s", help="状态过滤")
@click.option("--limit", "-l", default=10, help="返回数量")
def crm_list(status, limit):
    """获取客户列表

    \b
    示例:
      crm list
      crm list --status 跟进中
    """
    access_token = client_config.get_access_token()

    params = {"limit": limit}
    if status:
        params["status"] = status
    if access_token:
        params["user_access_token"] = access_token

    result = run_async(http_client.get("/api/v1/crm/list", params=params))
    print_result(result, "客户列表")
