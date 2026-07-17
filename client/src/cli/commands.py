"""CLI命令注册"""

from commands.health import health_commands
from commands.auth import auth_group
from commands.todo import todo_command
from commands.meet import meet_command
from commands.brief import brief_command
from commands.track import track_command
from commands.cost import cost_command
from commands.audit import audit_group
from commands.execute import execute_command
from commands.remind import remind_group
from commands.form import form_command
from commands.crm import crm_group
from commands.admin import admin_group


def register_all_commands(cli):
    """注册所有命令到CLI组"""
    cli.add_command(health_commands)
    cli.add_command(auth_group)
    cli.add_command(todo_command)
    cli.add_command(meet_command)
    cli.add_command(brief_command)
    cli.add_command(track_command)
    cli.add_command(cost_command)
    cli.add_command(audit_group)
    cli.add_command(execute_command)
    cli.add_command(remind_group)
    cli.add_command(form_command)
    cli.add_command(crm_group)
    cli.add_command(admin_group)
