"""健康检查命令

用于验证CLI→后端→飞书/LLM的通信链路。
"""

import click
from cli.app import run_async, print_result
from utils.http_client import http_client


@click.group(name="health")
def health_commands():
    """系统健康检查命令组"""
    pass


@health_commands.command(name="basic")
def health_basic():
    """基础健康检查"""
    result = run_async(http_client.health())
    print_result(result, "基础健康检查")


@health_commands.command(name="db")
def health_db():
    """数据库连接检查"""
    result = run_async(http_client.health_db())
    print_result(result, "数据库连接检查")


@health_commands.command(name="feishu")
def health_feishu():
    """飞书API连接检查"""
    result = run_async(http_client.health_feishu())
    print_result(result, "飞书API连接检查")


@health_commands.command(name="llm")
def health_llm():
    """LLM连接检查"""
    result = run_async(http_client.health_llm())
    print_result(result, "LLM连接检查")


@health_commands.command(name="full")
def health_full():
    """全链路健康检查"""
    result = run_async(http_client.health_full())
    print_result(result, "全链路健康检查")
