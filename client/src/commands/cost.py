"""成本录入 CLI 命令

/cost <项目名> <金额> <分类> - 录入成本并触发飞书审批
"""

import sys
import click
from cli.app import run_async
from utils.http_client import http_client
from utils.config import client_config


@click.command(name="cost")
@click.argument("project_name")
@click.argument("amount", type=float)
@click.argument("category")
@click.option("--description", "-d", help="费用描述/事由")
@click.option("--approval-code", "-a", help="审批定义码（不填自动匹配）")
@click.option("--title", "-t", help="审批标题（不填自动生成）")
def cost_command(project_name, amount, category, description, approval_code, title):
    """录入成本并触发飞书审批

    \b
    示例:
      cost "AI Agent项目" 5000 "人力成本"
      cost "AI Agent项目" 2000 "物料成本" -d "服务器费用"
      cost "数据分析项目" 800 "差旅费用" -a cost_xxx -t "7月差旅费报销"
    """
    access_token = client_config.get_access_token()
    open_id = client_config.get_open_id()

    payload = {
        "project_name": project_name,
        "amount": amount,
        "category": category,
        "description": description or "",
    }
    if approval_code:
        payload["approval_code"] = approval_code
    if title:
        payload["title"] = title
    if open_id:
        payload["open_id"] = open_id
    if access_token:
        payload["user_access_token"] = access_token

    result = run_async(http_client.post("/api/v1/cost", payload))

    sys.stdout.write("\n")
    sys.stdout.write("=" * 60 + "\n")
    sys.stdout.write("  成本录入结果\n")
    sys.stdout.write("=" * 60 + "\n")

    if not result or result.get("ok") is False:
        sys.stdout.write(f"  [X] {result.get('error', result.get('message', '未知错误'))}\n")
    else:
        if result.get("mock_mode"):
            sys.stdout.write("  [!] 模拟模式 - 审批未实际发起\n")
        else:
            sys.stdout.write("  [OK] 成本录入成功，审批流程已发起\n")
        sys.stdout.write(f"     项目：{result.get('project_name', '')}\n")
        sys.stdout.write(f"     金额：{result.get('amount', '')}\n")
        sys.stdout.write(f"     分类：{result.get('category', '')}\n")
        if result.get("description"):
            sys.stdout.write(f"     事由：{result.get('description', '')}\n")
        sys.stdout.write(f"     审批编码：{result.get('instance_code', '')}\n")
        sys.stdout.write(f"     审批标题：{result.get('title', '')}\n")
        if result.get("approval_url"):
            sys.stdout.write(f"     审批链接：{result.get('approval_url', '')}\n")

    sys.stdout.write("\n" + "=" * 60 + "\n")
    sys.stdout.write("\n")
    sys.stdout.flush()