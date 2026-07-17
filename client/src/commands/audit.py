"""审批处理 CLI 命令

audit tasks                        - 获取待审批任务列表（Admin权限）
audit pass <实例code> 理由         - 通过审批（Admin权限）
audit reject <实例code> 理由       - 拒绝审批（Admin权限）

也支持从 tasks 输出直接复制两个 ID：
audit pass <task_id> <实例code> 理由
audit reject <task_id> <实例code> 理由
"""

import sys
import click
from cli.app import run_async, print_result
from utils.http_client import http_client
from utils.config import client_config


@click.group(name="audit")
def audit_group():
    """审批处理（Admin权限）"""
    pass


def _parse_audit_args(first_id, args):
    """解析审批命令参数，自动识别 task_id + instance_code 组合

    audit pass <instance_code>           → instance_code, task_id=""
    audit pass <instance_code> 理由      → instance_code, task_id="", comment
    audit pass <task_id> <instance_code> → instance_code, task_id, comment=""
    audit pass <task_id> <instance_code> 理由 → instance_code, task_id, comment
    """
    if not args:
        return first_id, "", ""

    # 如果第二个参数包含 "-"（飞书 instance_code 是 UUID 格式），
    # 则认定用户复制了 task_id + instance_code 两个 ID
    if "-" in str(args[0]):
        task_id = first_id
        instance_id = args[0]
        comment_text = " ".join(args[1:]) if len(args) > 1 else ""
        return instance_id, task_id, comment_text

    # 否则第二个参数是理由的一部分
    return first_id, "", " ".join(args)


@audit_group.command(name="pass")
@click.argument("first_id")
@click.argument("args", required=False, nargs=-1)
def audit_pass(first_id, args):
    """通过审批

    \b
    示例:
      audit pass inst_001
      audit pass inst_001 同意，按流程执行
      audit pass 7662298... AA0AFA50-... 同意
    """
    instance_id, task_id, comment_text = _parse_audit_args(first_id, args)
    user_id = client_config.get_open_id()

    payload = {
        "action_type": "pass",
        "comment": comment_text,
        "user_id": user_id,
    }
    if task_id:
        payload["task_id"] = task_id

    result = run_async(http_client.post(f"/api/v1/audit/{instance_id}", payload))
    print_result(result, f"审批通过: {instance_id}")


@audit_group.command(name="reject")
@click.argument("first_id")
@click.argument("args", required=False, nargs=-1)
def audit_reject(first_id, args):
    """拒绝审批

    \b
    示例:
      audit reject inst_001
      audit reject inst_001 预算不足，需重新评估
      audit reject 7662298... AA0AFA50-... 预算不足
    """
    instance_id, task_id, comment_text = _parse_audit_args(first_id, args)
    user_id = client_config.get_open_id()

    payload = {
        "action_type": "reject",
        "comment": comment_text,
        "user_id": user_id,
    }
    if task_id:
        payload["task_id"] = task_id

    result = run_async(http_client.post(f"/api/v1/audit/{instance_id}", payload))
    print_result(result, f"审批拒绝: {instance_id}")


@audit_group.command(name="tasks")
@click.option("--topic", "-t", default=1, help="任务分组：1=待审批，2=已审批，3=我发起的")
@click.option("--definition-code", "-d", help="审批定义码过滤")
@click.option("--page-size", "-p", default=20, help="每页数量")
def audit_tasks(topic, definition_code, page_size):
    """获取待审批任务列表

    \b
    示例:
      audit tasks
      audit tasks --topic 1 --definition-code cost_approval
    """
    params = {
        "topic": topic,
        "page_size": page_size,
    }
    if definition_code:
        params["definition_code"] = definition_code

    result = run_async(http_client.get("/api/v1/audit/tasks", params=params))

    sys.stdout.write("\n")
    sys.stdout.write("=" * 60 + "\n")
    sys.stdout.write("  审批任务列表\n")
    sys.stdout.write("=" * 60 + "\n")

    if not result or result.get("ok") is False:
        sys.stdout.write(f"  [X] {result.get('error', '未知错误')}\n")
    else:
        sys.stdout.write("  [OK] Status: ok\n")
        sys.stdout.write(f"     信息: {result.get('message', '')}\n")

        if result.get("tasks"):
            tasks = result["tasks"]
            sys.stdout.write("\n")
            for i, task in enumerate(tasks):
                initiator_name = task.get("initiator_name", task.get("applicant", {}).get("name", "未知"))
                summaries = task.get("summaries", [])

                expense_type = ""
                expense_reason = ""
                expense_amount = ""

                for summary in summaries:
                    key = summary.get("key", "")
                    value = summary.get("value", "")
                    if "类型" in key or "报销类型" in key:
                        expense_type = value
                    elif "事由" in key or "原因" in key or "备注" in key:
                        expense_reason = value
                    elif "金额" in key or "费用" in key:
                        expense_amount = value

                form = task.get("form", {})
                for k, v in form.items():
                    if isinstance(v, dict):
                        v = v.get("value", "")
                    if "类型" in k or "报销类型" in k:
                        expense_type = v
                    elif "事由" in k or "原因" in k:
                        expense_reason = v
                    elif "金额" in k or "费用" in k:
                        expense_amount = v

                initiator_name = str(initiator_name).replace("\n", " ").strip()
                expense_type = str(expense_type).replace("\n", " ").strip()
                expense_reason = str(expense_reason).replace("\n", " ").strip()
                expense_amount = str(expense_amount).replace("\n", " ").strip()

                task_id = task.get("task_id", "")
                instance_code = task.get("instance_code", "")

                sys.stdout.write(f"  {i+1}. 申请人：{initiator_name} 报销类型：{expense_type} 报销事由：{expense_reason} 报销金额：{expense_amount}\n")
                sys.stdout.write(f"     task_id: {task_id}  instance: {instance_code}\n")
                sys.stdout.write(f"     审批命令: audit pass {task_id} {instance_code} 理由\n")

    sys.stdout.write("\n" + "=" * 60 + "\n")
    sys.stdout.write("\n")
    sys.stdout.flush()