"""管理员 CLI 命令

/admin users          - 列出所有用户
/admin set-role       - 设置用户角色（admin/member）
"""

import click
from cli.app import run_async, print_result
from utils.http_client import http_client


@click.group(name="admin")
def admin_group():
    """管理员工具（需要 Admin 权限）"""
    pass


@admin_group.command(name="users")
def list_users():
    """列出所有用户及角色"""
    result = run_async(http_client.get("/api/v1/admin/users"))
    if not result.get("ok"):
        print(f"✗ {result.get('error', '获取失败')}")
        return

    users = result.get("users", [])
    if not users:
        print("暂无用户")
        return

    print(f"\n{'ID':<5} {'姓名':<12} {'角色':<12} {'Open ID'}")
    print("-" * 80)
    for u in users:
        role_tag = "Admin" if u["role"] == "admin" else "Member"
        print(f"{u['id']:<5} {u['name']:<12} {role_tag:<12} {u['open_id']}")


@admin_group.command(name="set-role")
@click.argument("open_id")
@click.argument("role", type=click.Choice(["admin", "member"]))
def set_role(open_id, role):
    """设置用户角色

    OPEN_ID: 用户的飞书 Open ID（ou_开头）
    ROLE: admin 或 member

    \b
    示例:
      admin set-role ou_xxx admin     # 提升为管理员
      admin set-role ou_xxx member    # 降级为普通成员
    """
    result = run_async(http_client.post("/api/v1/admin/users/set-role", {
        "open_id": open_id,
        "role": role,
    }))
    if result.get("ok"):
        print(f"✓ {result.get('message', '操作成功')}")
    else:
        error_msg = result.get("error") or result.get("detail", "操作失败")
        print(f"✗ {error_msg}")