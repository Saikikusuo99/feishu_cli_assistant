"""项目进度追踪服务

处理 /track 指令的业务逻辑：
- 根据项目名自动查找对应的多维表格
- 从多维表格读取任务状态
- AI分析完成率、延迟率、风险项
- 生成可视化进度报告（飞书卡片）
- 自动推送报告至指定群聊或私聊
"""

import logging
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

from server.src.feishu.client import feishu_client
from server.src.ai.llm import llm_engine
from server.src.ai.prompt import PROJECT_TRACK_PROMPT, TRACK_SUMMARY_PROMPT, parse_ai_json

logger = logging.getLogger("lanshan-server.track_service")


def _parse_bitable_url(url: str) -> dict | None:
    """从飞书多维表格 URL 中解析 app_token 和 table_id
    
    支持格式：
    - https://xxx.feishu.cn/base/APP_TOKEN?table=TABLE_ID
    - https://xxx.feishu.cn/base/APP_TOKEN?table=TABLE_ID&view=VIEW_ID
    
    Returns:
        {"app_token": "...", "table_id": "..."} 或 None
    """
    if not url.startswith("http://") and not url.startswith("https://"):
        return None

    try:
        parsed = urlparse(url)
        # 从路径中提取 app_token: /base/APP_TOKEN
        path_match = re.search(r'/base/([A-Za-z0-9]+)', parsed.path)
        if not path_match:
            return None

        app_token = path_match.group(1)
        # 从查询参数中提取 table_id
        query_params = parse_qs(parsed.query)
        table_id = query_params.get("table", [None])[0]

        if not table_id:
            return None

        logger.info(f"从 URL 解析到: app_token={app_token}, table_id={table_id}")
        return {"app_token": app_token, "table_id": table_id}
    except Exception as e:
        logger.warning(f"解析 URL 失败: {e}")
        return None


async def _find_bitable_by_project_name(project_name: str, user_access_token: str = "") -> dict | None:
    """根据项目名通过 Drive API 搜索对应的多维表格
    
    Returns:
        {"app_token": "...", "table_id": "..."} 或 None
    """
    if not user_access_token:
        logger.warning("未提供 user_access_token，无法搜索多维表格")
        return None

    try:
        page_token = ""
        while True:
            params = {"type": "bitable", "page_size": 50}
            if page_token:
                params["page_token"] = page_token

            search_result = await feishu_client._request(
                "GET",
                "/drive/v1/files",
                user_access_token=user_access_token,
                params=params,
            )

            if search_result.get("code") != 0:
                break

            files = search_result.get("data", {}).get("files", [])

            for file in files:
                file_name = file.get("name", "").lstrip("/")
                app_token = file.get("token", "")

                if project_name == file_name or project_name in file_name or file_name in project_name:
                    logger.info(f"通过Drive API找到多维表格: {file_name} ({app_token})")
                    return {
                        "app_token": app_token,
                        "table_id": "",
                    }

            # 检查是否有更多页
            has_more = search_result.get("data", {}).get("has_more", False)
            page_token = search_result.get("data", {}).get("page_token", "")
            if not has_more:
                break

        logger.warning(f"Drive API 未找到匹配 '{project_name}' 的多维表格")
        return None
    except Exception as e:
        logger.warning(f"搜索多维表格失败: {e}")
        return None


async def get_bitable_info(app_token: str, user_access_token: str = "") -> dict:
    """获取多维表格信息（数据表列表和字段信息）
    
    Args:
        app_token: 多维表格应用token
        user_access_token: 用户访问令牌
    
    Returns:
        包含tables和fields信息的字典
    """
    result = {
        "ok": False,
        "tables": [],
        "fields": {},
        "error": "",
    }

    if not feishu_client.is_configured:
        result["error"] = "飞书客户端未配置"
        return result

    tables_result = await feishu_client.bitable_list_tables(
        app_token=app_token,
        user_access_token=user_access_token,
    )

    if tables_result.get("error") or tables_result.get("code") != 0:
        result["error"] = f"获取数据表列表失败: {tables_result.get('msg', '未知错误')}"
        return result

    tables = tables_result.get("data", {}).get("items", [])
    result["tables"] = tables
    logger.info(f"获取到 {len(tables)} 个数据表")

    for table in tables:
        table_id = table.get("table_id", "")
        if not table_id:
            continue

        fields_result = await feishu_client.bitable_get_schema(
            app_token=app_token,
            table_id=table_id,
            user_access_token=user_access_token,
        )

        if fields_result.get("code") == 0:
            fields = fields_result.get("data", {}).get("items", [])
            result["fields"][table_id] = fields
            logger.info(f"数据表 {table.get('name', '')} 包含 {len(fields)} 个字段")

    result["ok"] = True
    return result


def _generate_progress_card(project_name: str, summary: dict) -> dict:
    """生成飞书卡片可视化进度报告

    使用 JSON 2.0 格式，包含原生 chart 饼图和完整报告。
    """
    total = summary.get("total_tasks", 0) or 1
    completed = summary.get("completed_tasks", 0) or 0
    in_progress = summary.get("in_progress_tasks", 0) or 0
    delayed = summary.get("delayed_tasks", 0) or 0
    not_started = summary.get("not_started_tasks", 0) or 0

    completion_rate = summary.get("completion_rate", 0) or 0
    delay_rate = summary.get("delay_rate", 0) or 0

    completed_pct = round((completed / total) * 100) if total > 0 else 0
    in_progress_pct = round((in_progress / total) * 100) if total > 0 else 0
    delayed_pct = round((delayed / total) * 100) if total > 0 else 0
    not_started_pct = 100 - completed_pct - in_progress_pct - delayed_pct
    if not_started_pct < 0:
        not_started_pct = 0

    # 饼图数据（VChart 格式，与飞书卡片可视化文档一致）
    pie_data = [
        {"type": "已完成", "value": completed_pct},
        {"type": "进行中", "value": in_progress_pct},
        {"type": "已延迟", "value": delayed_pct},
        {"type": "未开始", "value": not_started_pct},
    ]

    body_elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**报告日期**: {datetime.now().strftime('%Y年%m月%d日')}",
            },
        },
        {
            "tag": "hr",
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "### 任务状态分布",
            },
        },
        {
            "tag": "chart",
            "aspect_ratio": "4:3",
            "color_theme": "brand",
            "chart_spec": {
                "type": "pie",
                "title": {
                    "text": "任务状态分布",
                },
                "data": {
                    "values": pie_data,
                },
                "valueField": "value",
                "categoryField": "type",
                "outerRadius": 0.9,
                "legends": {
                    "visible": True,
                    "orient": "right",
                },
                "padding": {
                    "left": 10,
                    "top": 10,
                    "bottom": 5,
                    "right": 0,
                },
                "label": {
                    "visible": True,
                },
            },
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    f"🟢 已完成: {completed}项 ({completed_pct}%)  |  "
                    f"🔵 进行中: {in_progress}项 ({in_progress_pct}%)\n"
                    f"🟡 已延迟: {delayed}项 ({delayed_pct}%)  |  "
                    f"⚪ 未开始: {not_started}项 ({not_started_pct}%)\n"
                    f"**总任务**: {total} 项 | **完成率**: {completion_rate}% | **延迟率**: {delay_rate}%"
                ),
            },
        },
        {
            "tag": "hr",
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "### 报告内容",
            },
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "详细报告请查看下方文字消息",
            },
        },
        {
            "tag": "hr",
        },
        {
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": "--- 由 AI 智能生成 ---",
                "text_align": "right",
            },
        },
    ]

    card = {
        "schema": "2.0",
        "config": {
            "wide_screen_mode": True,
            "enable_forward": True,
        },
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"{project_name} - 项目进度周报",
            },
            "template": "purple",
        },
        "body": {
            "elements": body_elements,
        },
    }

    return card


async def generate_progress_report(
    input_text: str,
    chat_id: str = "",
    user_access_token: str = "",
) -> dict:
    """生成项目进度周报并通过机器人发送

    支持两种模式：
    1. 自然语言模式：通过 Drive API 搜索多维表格，遍历所有数据表
    2. URL 模式：解析飞书多维表格链接，分析指定表格

    Args:
        input_text: 项目名称（自然语言）或飞书多维表格 URL
        chat_id: 消息接收者的 open_id
        user_access_token: 用户访问令牌
    """
    try:
        bitable_info = None
        bitable_config = None
        app_token = None
        table_id = None
        project_name = input_text

        # 检测是否为 URL 模式
        url_config = _parse_bitable_url(input_text)
        if url_config:
            app_token = url_config["app_token"]
            table_id = url_config["table_id"]
            project_name = f"多维表格 {app_token[:8]}..."
            logger.info(f"URL 模式：app_token={app_token}, table_id={table_id}")
        else:
            # 自然语言模式：通过 Drive API 搜索
            project_name = input_text
            bitable_config = await _find_bitable_by_project_name(project_name, user_access_token)
            if not bitable_config:
                return {
                    "ok": False,
                    "error": f"未找到项目 '{project_name}' 的多维表格，请确认项目名称正确",
                }
            app_token = bitable_config["app_token"]
            table_id = bitable_config["table_id"]
            logger.info(f"根据项目名 '{project_name}' 找到多维表格配置")

        if not app_token:
            return {"ok": False, "error": "未找到多维表格 app_token"}

        if not feishu_client.is_configured:
            return {"ok": False, "error": "飞书客户端未配置"}

        # 确定要读取的数据表列表
        if table_id:
            table_ids = [table_id]
        else:
            bitable_info = await get_bitable_info(
                app_token=app_token,
                user_access_token=user_access_token,
            )
            if not bitable_info.get("ok") or not bitable_info.get("tables"):
                return {
                    "ok": False,
                    "error": f"获取数据表列表失败: {bitable_info.get('error', '未知错误')}",
                }
            table_ids = [t.get("table_id", "") for t in bitable_info["tables"]]
            table_ids = [tid for tid in table_ids if tid]
            if not table_ids:
                return {"ok": False, "error": "未找到有效的数据表"}
            logger.info(
                f"自动选择 {len(table_ids)} 个数据表: "
                f"{[t.get('name', '') for t in bitable_info['tables']]}"
            )

        # 遍历所有数据表，分页读取全部记录
        all_records = []
        for tid in table_ids:
            page_token = ""
            while True:
                result = await feishu_client.bitable_read_records(
                    app_token=app_token,
                    table_id=tid,
                    user_access_token=user_access_token,
                    page_size=100,
                    page_token=page_token if page_token else None,
                )

                if result.get("error"):
                    return {
                        "ok": False,
                        "error": f"读取多维表格记录失败: {result.get('msg', '未知错误')}",
                    }

                records = result.get("data", {}).get("items", [])
                all_records.extend(records)

                has_more = result.get("data", {}).get("has_more", False)
                page_token = result.get("data", {}).get("page_token", "")

                if not has_more:
                    break

        if not all_records:
            return {"ok": False, "error": "多维表格中无任务记录"}

        task_data = _format_task_data(all_records)
        logger.info(f"从 {len(table_ids)} 个数据表读取到 {len(all_records)} 条任务记录")

        # AI 分析：先统计汇总，再生成报告
        today = datetime.now().strftime("%Y-%m-%d")

        # 第一步：AI 统计汇总数据
        summary_prompt = TRACK_SUMMARY_PROMPT.format(task_data=task_data, today=today)
        summary_response = await llm_engine.generate(summary_prompt)
        summary_parsed = parse_ai_json(summary_response)
        summary = summary_parsed.get("summary", {}) if summary_parsed else {}

        # 如果 AI 统计失败，从原始数据中手动计算
        if not summary:
            logger.warning("AI 统计汇总失败，使用手动计算")
            summary = _compute_summary_from_records(all_records)

        # 第二步：AI 生成专业 PM 周报
        report_prompt = PROJECT_TRACK_PROMPT.format(task_data=task_data, today=today)
        report_text = await llm_engine.generate(report_prompt)

        if not report_text:
            return {
                "ok": False,
                "error": "AI无法生成进度报告",
            }

        # 生成飞书卡片（JSON 2.0，包含原生 chart 饼图，不含详细报告）
        card = _generate_progress_card(project_name, summary)

        # 发送消息：先发卡片（饼图+概要），再发文字详细报告
        message_sent = False
        message_id = ""

        if chat_id and feishu_client.is_configured:
            receive_id_type = "chat_id" if chat_id.startswith("oc_") else "open_id"

            # 1. 发送卡片（饼图+概要统计，使用应用身份 token）
            send_result = await feishu_client.send_message(
                receive_id=chat_id,
                content=json.dumps(card),
                msg_type="interactive",
                receive_id_type=receive_id_type,
                use_app_token=True,
            )
            if send_result.get("error") or send_result.get("code") != 0:
                logger.warning(f"发送卡片失败({send_result.get('msg', '')})，尝试发送文本消息")
                text_content = report_text[:2000]
                send_result = await feishu_client.send_message(
                    receive_id=chat_id,
                    content=json.dumps({"text": text_content}),
                    msg_type="text",
                    receive_id_type=receive_id_type,
                    use_app_token=True,
                )
                if send_result.get("ok") or send_result.get("code") == 0:
                    message_sent = True
                    message_id = send_result.get("data", {}).get("message_id", "")
                    logger.info(f"文本报告已发送至 {receive_id_type} {chat_id}")
            else:
                message_sent = True
                message_id = send_result.get("data", {}).get("message_id", "")
                logger.info(f"卡片报告已发送至 {receive_id_type} {chat_id}")

                # 2. 发送详细文字报告（使用应用身份 token）
                try:
                    text_result = await feishu_client.send_message(
                        receive_id=chat_id,
                        content=json.dumps({"text": report_text[:4000]}),
                        msg_type="text",
                        receive_id_type=receive_id_type,
                        use_app_token=True,
                    )
                    if text_result.get("ok") or text_result.get("code") == 0:
                        logger.info(f"详细文字报告已发送至 {receive_id_type} {chat_id}")
                    else:
                        logger.warning(f"文字报告发送失败: {text_result.get('msg', '')}")
                except Exception as e:
                    logger.warning(f"发送文字报告异常: {e}")

        return {
            "ok": True,
            "message": "项目进度报告生成成功",
            "project_name": project_name,
            "card": card,
            "summary": summary,
            "report_text": report_text,
            "message_sent": message_sent,
            "message_id": message_id,
            "raw_task_data": task_data,
            "bitable_info": bitable_info,
            "bitable_config": bitable_config,
        }

    except Exception as e:
        logger.error(f"生成进度报告失败: {e}")
        return {"ok": False, "error": str(e)}


def _compute_summary_from_records(records: list) -> dict:
    """从原始记录中手动计算汇总统计（AI 不可用时的降级方案）"""
    total = len(records)
    completed = 0
    in_progress = 0
    delayed = 0
    not_started = 0

    for record in records:
        fields = record.get("fields", {})
        status = fields.get("进展", fields.get("状态", fields.get("status", "")))
        if isinstance(status, list):
            status = status[0] if status else ""
        status = str(status).strip()

        if any(kw in status for kw in ["已完成", "完成", "done", "completed"]):
            completed += 1
        elif any(kw in status for kw in ["延迟", "延期", "delayed", "delay"]):
            delayed += 1
        elif any(kw in status for kw in ["进行中", "进行", "in_progress", "doing"]):
            in_progress += 1
        else:
            not_started += 1

    return {
        "total_tasks": total,
        "completed_tasks": completed,
        "in_progress_tasks": in_progress,
        "not_started_tasks": not_started,
        "delayed_tasks": delayed,
        "completion_rate": round((completed / total) * 100) if total > 0 else 0,
        "delay_rate": round((delayed / total) * 100) if total > 0 else 0,
    }


def _format_task_data(records: list) -> str:
    """格式化多维表格任务数据为文本
    
    支持中英文字段名映射（按优先级依次尝试）：
    - name: 需求, 任务描述, 任务名称, task_name
    - status: 状态, 进展, status
    - priority: 优先级, 重要紧急程度, priority
    - due_date: 截止时间, 截止日期, 预计完成日期, due_date
    - owner: 研发人员, 任务执行人, 负责人, owner
    - is_delayed: 是否延期
    """
    if not records:
        return "[]"

    task_list = []
    for record in records:
        fields = record.get("fields", {})
        
        name = fields.get("需求", fields.get("任务描述", fields.get("任务名称", fields.get("task_name", ""))))

        status = fields.get("状态", fields.get("进展", fields.get("status", "")))
        # 展开选择字段的列表格式（如 ["已完成"] → "已完成"）
        if isinstance(status, list):
            status = status[0] if status else ""
        status = str(status).strip()

        priority = fields.get("优先级", fields.get("重要紧急程度", fields.get("priority", "")))
        if isinstance(priority, list):
            priority = priority[0] if priority else ""
        priority = str(priority).strip()

        due_date = fields.get("截止时间", fields.get("截止日期", fields.get("预计完成日期", fields.get("due_date", ""))))
        if isinstance(due_date, int):
            try:
                dt = datetime.fromtimestamp(due_date / 1000, tz=timezone.utc)
                due_date = dt.strftime("%Y-%m-%d")
            except Exception:
                due_date = ""

        owner_field = fields.get("研发人员", fields.get("任务执行人", fields.get("负责人", fields.get("owner", ""))))
        owner = ""
        if isinstance(owner_field, list) and owner_field:
            owner = owner_field[0].get("name", "")
        elif isinstance(owner_field, dict):
            owner = owner_field.get("name", "")
        else:
            owner = str(owner_field)

        # 也提取开始时间作为参考
        start_date = fields.get("开始时间", "")
        if isinstance(start_date, int):
            try:
                dt = datetime.fromtimestamp(start_date / 1000, tz=timezone.utc)
                start_date = dt.strftime("%Y-%m-%d")
            except Exception:
                start_date = ""

        is_delayed = fields.get("是否延期", "")
        delayed_text = ""
        if isinstance(is_delayed, list) and is_delayed:
            for item in is_delayed:
                if isinstance(item, dict):
                    delayed_text = item.get("text", "")
                    break

        task_info = {
            "name": name,
            "status": status,
            "priority": priority,
            "due_date": due_date,
            "owner": owner,
        }
        if start_date:
            task_info["start_date"] = start_date
        if delayed_text:
            task_info["is_delayed"] = delayed_text

        task_list.append(task_info)

    return json.dumps(task_list, ensure_ascii=False)
