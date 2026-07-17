"""柔性催办 CLI 命令

remind <自然语言>     - AI智能催办（支持自然语言解析）
remind overdue        - 查询逾期任务列表
"""

import click
from cli.app import run_async, print_result
from utils.http_client import http_client
from utils.config import client_config


@click.group(name="remind", invoke_without_command=True)
@click.argument("user_input", nargs=-1, required=False)
@click.pass_context
def remind_group(ctx, user_input):
    """柔性催办（Admin权限）

    \b
    直接输入自然语言进行AI智能催办:
      remind 催办绿大萌的作业批改任务
      remind 提醒张三完成周报

    \b
    查询逾期任务:
      remind overdue
    """
    if ctx.invoked_subcommand is None:
        input_text = " ".join(user_input) if user_input else ""
        if not input_text:
            click.echo("请输入催办内容，例如: remind 催办绿大萌的作业批改任务")
            click.echo("或者使用 remind overdue 查看逾期任务")
            return

        access_token = client_config.get_access_token()

        payload = {
            "user_input": input_text,
        }
        if access_token:
            payload["user_access_token"] = access_token

        result = run_async(http_client.post("/api/v1/remind/natural", payload))
        print_result(result, f"智能催办: {input_text}")


@remind_group.command(name="overdue")
def remind_overdue():
    """查询逾期任务列表

    \b
    示例:
      remind overdue
    """
    result = run_async(http_client.get("/api/v1/remind/overdue"))

    if result.get("ok"):
        print("=" * 60)
        print(f"  逾期任务列表（共 {result.get('total_count', 0)} 个）")
        print("=" * 60)

        tasks = result.get("tasks", [])
        if tasks:
            for i, task in enumerate(tasks, 1):
                print(f"\n  [{i}] {task.get('summary', '未命名任务')}")
                print(f"     ├── 任务ID: {task.get('task_id', '')}")
                print(f"     ├── 截止时间: {task.get('due_time', '')}")
                print(f"     ├── 逾期天数: {task.get('overdue_days', 0)}天")
                print(f"     ├── 状态: {task.get('status', '')}")

                assignees = task.get("assignees", [])
                if assignees:
                    assignee_names = [a.get("name", "未知") for a in assignees]
                    print(f"     └── 参与人: {', '.join(assignee_names)}")
                else:
                    print(f"     └── 参与人: 无")
        else:
            print("\n  ✓ 暂无逾期任务")

        print("\n" + "=" * 60)
    else:
        print_result(result, "逾期任务列表")
