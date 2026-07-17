"""AI任务拆解引擎 CLI 命令

execute "<目标>" - AI拆解目标并交互式执行（Admin权限）
"""

import click
from cli.app import run_async
from utils.http_client import http_client
from utils.config import client_config


@click.command(name="execute")
@click.argument("goal")
def execute_command(goal):
    """AI拆解目标并交互式执行

    \b
    示例:
      execute "组织一场50人的技术沙龙"
      execute "完成项目上线准备"
    """
    access_token = client_config.get_access_token()
    user_open_id = client_config.get_open_id()

    # ==================== Step 1: AI拆解任务 ====================
    payload = {"goal": goal}
    if access_token:
        payload["user_access_token"] = access_token
    if user_open_id:
        payload["user_open_id"] = user_open_id

    result = run_async(http_client.post("/api/v1/execute", payload))

    if not result.get("ok"):
        click.echo(f"\n❌ 任务拆解失败: {result.get('error', '未知错误')}")
        return

    tasks = result.get("tasks", [])
    goal_name = result.get("goal_name", goal)
    has_survey = result.get("has_survey", False)
    execution_id = result.get("execution_id", "")
    notes = result.get("notes", [])

    # 显示任务表格
    click.echo(f"\n✅ 任务拆解完成，以下任务将写入多维表格「需求」字段：")
    click.echo(f"   活动名称: {goal_name}")
    click.echo(f"   摘要: {result.get('summary', '')}")
    click.echo()
    click.echo(f"   {'序号':<6} {'需求内容':<20} {'优先级':<8} {'状态':<8}")
    click.echo(f"   {'─'*6} {'─'*20} {'─'*8} {'─'*8}")
    for i, task in enumerate(tasks, 1):
        click.echo(f"   {i:<6} {task.get('name', ''):<20} {task.get('priority', 'P2'):<8} 未开始")
    click.echo()
    if has_survey:
        click.echo("   📋 检测到需要创建问卷")
    if notes:
        click.echo(f"   注意事项: {', '.join(notes)}")

    # 询问确认
    confirmed = click.confirm("\n❓ 是否同意该计划？", default=True)
    if not confirmed:
        click.echo("已取消执行。")
        return

    # ==================== Step 2-5: 执行计划 ====================
    click.echo(f"\n▶️ 开始执行计划...")

    payload = {
        "execution_id": execution_id,
    }
    if access_token:
        payload["user_access_token"] = access_token
    if user_open_id:
        payload["user_open_id"] = user_open_id
    if client_config.user_name:
        payload["user_name"] = client_config.user_name

    result = run_async(http_client.post("/api/v1/execute/run", payload))

    if not result.get("ok"):
        error = result.get("error", "")
        created_resources = result.get("created_resources", [])
        click.echo(f"\n❌ 执行失败: {error}")
        if created_resources:
            click.echo("⚠️ 已创建资源:")
            for r in created_resources:
                click.echo(f"   ├── {r.get('type')}: {r.get('name')} (id: {r.get('id')})")
        return

    # 显示完成结果
    click.echo(f"\n🎉 执行计划全部完成！")
    results = result.get("results", {})
    chat = results.get("chat", {})
    bitable = results.get("bitable", {})
    announcement = results.get("announcement", {})
    survey = results.get("survey")

    click.echo("┌───────────────────────────────────────────────────────┐")
    click.echo("│ 执行结果:                                              │")
    click.echo(f"│  ├── 群聊: {chat.get('name', '')}")
    click.echo(f"│  ├── 进度表格: {bitable.get('url', '')}")
    click.echo(f"│  ├── 活动公告: {'已发布' if announcement.get('published') else '未发布'}")
    if survey:
        click.echo(f"│  └── 报名问卷: {survey.get('url', '')}")
    click.echo("├───────────────────────────────────────────────────────┤")
    click.echo(f"│ 多维表格已写入 {bitable.get('records_written', 0)} 条任务记录")
    click.echo("│ 群成员已自动获得表格编辑权限")
    click.echo("└───────────────────────────────────────────────────────┘")