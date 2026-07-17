"""文档摘要服务

处理 /brief 指令的业务逻辑：读取文档内容 → AI生成摘要 → 可选创建多维表格。
多维表格使用「任务分配」表结构作为预设表格(与bitable_template/create_template.py一致)。
"""

import logging

from server.src.feishu.client import feishu_client
from server.src.ai.llm import llm_engine
from server.src.ai.prompt import DOCUMENT_SUMMARY_PROMPT, parse_ai_json

logger = logging.getLogger("lanshan-server.brief_service")

# brief功能预设表格字段定义(与bitable_template/create_template.py的任务分配表一致)
PRESET_TABLE_NAME = "任务分配"
PRESET_FIELDS = [
    {"field_name": "任务描述", "type": 1},  # Text, 主键
    {"field_name": "优先级", "type": 3, "ui_type": "SingleSelect",
     "property": {"options": [{"name": "高", "color": 11}, {"name": "中", "color": 1}, {"name": "低", "color": 2}]}},
    {"field_name": "状态", "type": 3, "ui_type": "SingleSelect",
     "property": {"options": [{"name": "未开始", "color": 18}, {"name": "进行中", "color": 13}, {"name": "已完成", "color": 15}]}},
    {"field_name": "负责人", "type": 1},  # Text (AI生成文本名，非User类型)
    {"field_name": "开始日期", "type": 5, "ui_type": "DateTime",
     "property": {"auto_fill": False, "date_formatter": "yyyy/MM/dd"}},
    {"field_name": "截止日期", "type": 5, "ui_type": "DateTime",
     "property": {"auto_fill": False, "date_formatter": "yyyy/MM/dd"}},
    {"field_name": "备注", "type": 1},  # Text
]


async def generate_brief(
    doc_url: str,
    create_table: bool = False,
    user_open_id: str = "",
    user_access_token: str = "",
) -> dict:
    """生成文档摘要并可选创建任务多维表格

    Args:
        doc_url: 文档URL
        create_table: 是否创建多维表格
        user_open_id: 用户open_id
        user_access_token: 用户访问令牌
    """
    try:
        from server.src.services.auth_service import oauth_service

        if not user_access_token and user_open_id:
            refreshed = await oauth_service.get_user_token_by_open_id_async(user_open_id)
            if refreshed:
                user_access_token = refreshed
                logger.info(f"通过服务端自动刷新获取到用户token: {user_open_id}")

        # 1. 读取文档内容
        content = ""
        doc_title = ""
        if feishu_client.is_configured:
            try:
                doc_token, doc_type = _extract_doc_token(doc_url)
                if doc_token:
                    if doc_type == "file":
                        result = await feishu_client.fetch_file_content(doc_token, user_access_token=user_access_token)
                    else:
                        result = await feishu_client.fetch_document(doc_token, user_access_token=user_access_token)
                    doc_data = result.get("data", {})
                    content = doc_data.get("content", "")
                    doc_title = doc_data.get("title", "")

                    # 文档内容截断（避免超过LLM上下文限制）
                    if len(content) > 8000:
                        content = content[:8000] + "...(内容已截断)"
                        logger.info(f"文档内容过长，已截断至8000字符")
            except Exception as e:
                logger.warning(f"获取飞书文档失败: {e}")
                # 将用户输入作为文本内容使用
                content = doc_url

        if not content:
            content = doc_url  # 飞书未配置时，将URL作为模拟内容

        logger.info(f"文档内容长度: {len(content)} 字符")

        # 2. AI 生成摘要和任务拆解
        from datetime import date
        today = date.today().strftime("%Y-%m-%d")
        prompt = DOCUMENT_SUMMARY_PROMPT.format(
            doc_title=doc_title or "未知文档",
            today=today,
            content=content[:8000],
        )
        ai_response = await llm_engine.generate(prompt)
        parsed = parse_ai_json(ai_response)

        if not parsed:
            return {
                "ok": False,
                "error": "AI无法生成文档摘要",
                "raw_response": ai_response,
            }

        summary = parsed.get("summary", "")
        tasks = parsed.get("tasks", [])

        result = {
            "ok": True,
            "message": f"文档摘要生成成功（拆解出 {len(tasks)} 个任务）",
            "summary": summary,
            "tasks": tasks,
            "doc_url": doc_url,
            "doc_title": doc_title,
        }

        # 3. 可选：创建多维表格
        if create_table and tasks:
            table_info = await _create_task_bitable(tasks, doc_url, doc_title=doc_title, user_access_token=user_access_token)
            if table_info:
                result["table"] = table_info

                # 4. 将多维表格发送给用户
                if user_open_id:
                    send_result = await _send_table_to_user(
                        user_open_id,
                        table_info,
                        summary,
                        tasks,
                        user_access_token=user_access_token,
                    )
                    if send_result:
                        result["message_sent"] = send_result
                        logger.info(f"多维表格已发送给用户: {user_open_id}")
                    else:
                        logger.warning(f"发送多维表格给用户失败: {user_open_id}")

        return result

    except Exception as e:
        logger.error(f"生成文档摘要失败: {e}")
        return {"ok": False, "error": str(e)}


def _extract_doc_token(doc_url: str) -> tuple[str | None, str]:
    """从飞书文档URL中提取token和类型

    Returns:
        (token, doc_type): token和类型(docx/wiki/file)，提取失败返回(None, "")
    """
    import re
    # 匹配模式: https://xxx.feishu.cn/docx/XXXXX
    match = re.search(r"/docx/([A-Za-z0-9]+)", doc_url)
    if match:
        return match.group(1), "docx"
    # 匹配模式: https://xxx.feishu.cn/wiki/XXXXX
    match = re.search(r"/wiki/([A-Za-z0-9]+)", doc_url)
    if match:
        return match.group(1), "wiki"
    # 匹配模式: https://xxx.feishu.cn/file/XXXXX
    match = re.search(r"/file/([A-Za-z0-9]+)", doc_url)
    if match:
        return match.group(1), "file"
    return None, ""


async def _create_task_bitable(tasks: list, doc_url: str, doc_title: str = "", user_access_token: str = "") -> dict | None:
    """创建任务拆解多维表格（使用任务分配表结构作为预设）

    创建流程：
    1. 创建多维表格应用
    2. 创建新数据表（含主键字段「任务描述」）
    3. 删除应用自动生成的默认空白表
    4. 添加剩余预设字段
    5. 添加公式字段「任务所需时长」
    6. 创建4个视图：任务总表(grid)、新增任务(form)、负责人看板(kanban)、任务进展甘特图(gantt)
    7. 写入任务数据
    """
    try:
        from datetime import datetime
        from urllib.parse import unquote
        from server.src.services.auth_service import oauth_service

        if not feishu_client.is_configured:
            return {
                "mock": True,
                "name": f"任务分配_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "url": "[模拟] 多维表格链接（飞书未配置）",
                "task_count": len(tasks),
            }

        # 解码文档标题（去除URL编码和文件扩展名）
        clean_title = unquote(doc_title) if doc_title else "文档"
        # 去掉文件扩展名
        if "." in clean_title:
            clean_title = clean_title.rsplit(".", 1)[0]
        table_name = f"{clean_title}任务拆解"

        app_name = table_name  # 应用名与表名一致，用户看到的就是「xxx任务拆解」

        # 1. 创建多维表格应用（优先使用用户身份）
        logger.info(f"创建多维表格应用: {app_name}")
        app_result = await feishu_client.bitable_create_app(app_name, user_access_token=user_access_token)
        app_data = app_result.get("data", {}).get("app", {})
        app_token = app_data.get("app_token", "")
        app_url = app_data.get("url", "")

        if not app_token:
            logger.warning(f"创建多维表格应用失败: {app_result}")
            return None

        logger.info(f"多维表格应用创建成功: {app_token}")

        # 如果token在上一步被刷新过，重新获取最新token
        token_info = oauth_service.get_stored_user_info()
        if token_info.get("ok"):
            fresh_token = token_info.get("access_token", "")
            if fresh_token and fresh_token != user_access_token:
                logger.info(f"检测到token已刷新，更新为最新token")
                user_access_token = fresh_token

        # 2. 创建新数据表（含主键字段「任务描述」）
        primary_field = PRESET_FIELDS[0]
        table_result = await feishu_client._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables",
            json={
                "table": {
                    "name": table_name,
                    "default_view_name": "任务总表",
                    "fields": [{"field_name": primary_field["field_name"], "type": primary_field["type"]}],
                }
            },
            user_access_token=user_access_token,
        )
        table_id = table_result.get("data", {}).get("table_id", "")
        if not table_id:
            logger.warning(f"创建数据表失败: {table_result}")
            return None

        logger.info(f"数据表创建成功: {table_name} (table_id: {table_id})")

        # 3. 删除应用自动生成的默认空白表
        await feishu_client._delete_default_tables(app_token, table_id, user_access_token)

        # 4. 添加剩余字段（跳过第一个字段「任务描述」，已在建表时创建）
        logger.info("创建预设字段...")
        start_date_field_id = None
        end_date_field_id = None
        for field in PRESET_FIELDS[1:]:
            body = {"field_name": field["field_name"], "type": field["type"]}
            if field.get("ui_type"):
                body["ui_type"] = field["ui_type"]
            if field.get("property"):
                body["property"] = field["property"]

            field_result = await feishu_client._request(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
                json=body,
                user_access_token=user_access_token,
            )
            field_data = field_result.get("data", {}).get("field", {})
            field_id = field_data.get("field_id", "")
            if field_id:
                logger.info(f"字段创建成功: {field['field_name']} -> {field_id}")
            else:
                logger.warning(f"字段创建失败: {field['field_name']}, 响应: {field_result}")

            if field["field_name"] == "开始日期":
                start_date_field_id = field_id
            elif field["field_name"] == "截止日期":
                end_date_field_id = field_id

        # 5. 添加公式字段: 任务所需时长 = 截止日期 - 开始日期
        if start_date_field_id and end_date_field_id:
            formula_expr = (
                f"bitable::$table[{table_id}].$field[{end_date_field_id}]"
                f"-bitable::$table[{table_id}].$field[{start_date_field_id}]"
            )
            formula_body = {
                "field_name": "任务所需时长",
                "type": 20,
                "ui_type": "Formula",
                "property": {"formatter": "0", "formula_expression": formula_expr},
            }
            formula_result = await feishu_client._request(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
                json=formula_body,
                user_access_token=user_access_token,
            )
            if formula_result.get("code") == 0:
                logger.info(f"公式字段创建成功: 任务所需时长")
            else:
                logger.warning(f"公式字段创建失败: {formula_result}")

        # 6. 创建4个视图：任务总表(grid)、新增任务(form)、负责人看板(kanban)、任务进展甘特图(gantt)
        logger.info("创建视图...")
        views = [
            {"view_name": "任务总表", "view_type": "grid"},
            {"view_name": "新增任务", "view_type": "form"},
            {"view_name": "负责人看板", "view_type": "kanban"},
            {"view_name": "任务进展甘特图", "view_type": "gantt"},
        ]
        for view in views:
            view_result = await feishu_client.bitable_create_view(
                app_token, table_id, view["view_name"], view["view_type"],
                user_access_token=user_access_token,
            )
            if view_result.get("code") == 0:
                logger.info(f"视图创建成功: {view['view_name']} ({view['view_type']})")
            else:
                logger.warning(f"视图创建失败: {view['view_name']} - {view_result.get('msg', '')}")

        # 7. 写入任务数据
        logger.info(f"写入任务数据，共 {len(tasks)} 条...")
        records = []
        for task in tasks:
            start_date = task.get("start_date", "")
            start_timestamp = ""
            if start_date:
                try:
                    dt = datetime.strptime(start_date, "%Y-%m-%d")
                    start_timestamp = int(dt.timestamp() * 1000)
                except:
                    start_timestamp = ""

            due_date = task.get("due_date", "")
            due_timestamp = ""
            if due_date:
                try:
                    dt = datetime.strptime(due_date, "%Y-%m-%d")
                    due_timestamp = int(dt.timestamp() * 1000)
                except:
                    due_timestamp = ""

            records.append({
                "fields": {
                    "任务描述": task.get("name", ""),
                    "优先级": task.get("priority", "中"),
                    "状态": "未开始",
                    "负责人": task.get("owner", ""),
                    "开始日期": start_timestamp,
                    "截止日期": due_timestamp,
                    "备注": task.get("notes", doc_url),
                }
            })

        if records:
            record_result = await feishu_client.bitable_add_records(
                app_token,
                table_id,
                records,
                user_access_token=user_access_token,
            )
            if record_result.get("code") == 0:
                logger.info(f"任务数据写入成功")
            else:
                logger.warning(f"任务数据写入失败: {record_result}")

        return {
            "name": app_name,
            "url": app_url,
            "app_token": app_token,
            "table_id": table_id,
            "task_count": len(tasks),
        }

    except Exception as e:
        logger.warning(f"创建多维表格失败: {e}")
        return None


async def _send_table_to_user(
    user_open_id: str,
    table_info: dict,
    summary: str,
    tasks: list,
    user_access_token: str = "",
) -> dict | None:
    """将多维表格链接发送给用户"""
    try:
        table_name = table_info.get("name", "")
        table_url = table_info.get("url", "")
        task_count = table_info.get("task_count", 0)

        task_list_text = "\n".join([f"  {i+1}. {t.get('name', '')} [优先级: {t.get('priority', '')}]" for i, t in enumerate(tasks[:5])])
        if len(tasks) > 5:
            task_list_text += f"\n  ... 还有 {len(tasks) - 5} 个任务"

        # 纯文字格式，不使用markdown语法
        text = (
            f"文档摘要任务拆解已完成！\n"
            f"\n"
            f"【文档摘要】\n{summary[:200]}{'...' if len(summary) > 200 else ''}\n"
            f"\n"
            f"【拆解任务】共 {task_count} 个\n"
            f"{task_list_text}\n"
            f"\n"
            f"【多维表格链接】\n{table_url}\n"
            f"\n"
            f"表格字段：任务描述 | 优先级 | 状态 | 负责人 | 开始日期 | 截止日期 | 任务所需时长(公式) | 备注"
        )

        content = {"text": text}

        import json
        result = await feishu_client.send_message(
            receive_id=user_open_id,
            content=json.dumps(content),
            msg_type="text",
            receive_id_type="open_id",
        )

        if result.get("code") == 0:
            return {
                "ok": True,
                "message": "消息发送成功",
                "msg_id": result.get("data", {}).get("message_id", ""),
            }
        else:
            logger.warning(f"发送消息失败: {result}")
            return None

    except Exception as e:
        logger.warning(f"发送消息给用户失败: {e}")
        return None
