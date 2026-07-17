"""文档摘要 CLI 命令

/brief <文档URL> - AI生成文档摘要并拆解任务
"""

import click
from cli.app import run_async, print_result
from utils.http_client import http_client


@click.command(name="brief")
@click.argument("doc_url")
def brief_command(doc_url):
    """生成文档摘要并拆解为可执行任务，自动创建多维表格

    \b
    示例:
      brief https://xxx.feishu.cn/docx/XXXXX
    """
    result = run_async(http_client.post("/api/v1/brief", {
        "doc_url": doc_url,
        "create_table": True,
    }))
    print_result(result, f"文档摘要: {doc_url}")
