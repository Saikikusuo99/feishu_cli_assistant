"""表单分析服务

处理 /form 指令的业务逻辑：
- 解析目标（URL 或 项目名）
- 导出多维表格数据
- 调用LLM分析数据趋势或满意度
- 通过机器人发送分析报告
- 返回分析结果
"""

import json
import logging
import re
from datetime import datetime

from server.src.feishu.client import feishu_client
from server.src.ai.llm import llm_engine
from server.src.ai.prompt import FORM_ANALYSIS_PROMPT, parse_ai_json

logger = logging.getLogger("lanshan-server.form_service")


async def resolve_bitable_target(target: str, user_access_token: str = "") -> dict | None:
    """解析多维表格目标

    支持两种输入：
    1. 飞书多维表格 URL（优先级1，精确解析）：
       - 带 table 参数：单表分析，返回 {"app_token": "...", "tables": [{"table_id": "...", "table_name": ""}]}
       - 不带 table 参数：多表分析，需后续获取所有数据表
    2. 项目名（优先级2，Drive API 模糊搜索）：
       按云盘内多维表格文件名模糊匹配，多表分析

    Returns:
        {"app_token": "...", "tables": [...]} 或 None
        - tables 为空列表：需要后续获取所有数据表（多表模式）
        - tables 有一个元素：单表模式
    """
    target = (target or "").strip()
    if not target:
        return None

    # 优先级1：URL 精确解析
    if target.startswith("http"):
        url_match = re.search(r"/base/([A-Za-z0-9]+)(?:\?.*?table=([A-Za-z0-9]+))?", target)
        if url_match:
            app_token = url_match.group(1)
            table_id = url_match.group(2) or ""
            if table_id:
                logger.info(f"从URL解析（单表）: app_token={app_token}, table_id={table_id}")
                return {"app_token": app_token, "tables": [{"table_id": table_id, "table_name": ""}]}
            else:
                logger.info(f"从URL解析（多表）: app_token={app_token}")
                return {"app_token": app_token, "tables": []}
        logger.warning(f"输入是URL但无法解析出app_token: {target}")
        return None

    # 优先级2：Drive API 模糊搜索（项目名 → 多表模式）
    if user_access_token:
        try:
            search_result = await feishu_client._request(
                "GET",
                "/drive/v1/files",
                user_access_token=user_access_token,
                params={"type": "bitable", "page_size": 50},
            )

            if search_result.get("code") == 0:
                files = search_result.get("data", {}).get("files", [])
                if not files:
                    logger.warning(f"Drive API 搜索结果为空，未找到任何多维表格")
                else:
                    # 优先精确匹配，再尝试模糊匹配
                    exact_match = None
                    partial_matches = []
                    for file in files:
                        file_name = file.get("name", "").lstrip("/")
                        app_token = file.get("token", "")
                        if not app_token:
                            continue
                        if target == file_name:
                            exact_match = (file_name, app_token)
                            break
                        if target in file_name or file_name in target:
                            partial_matches.append((file_name, app_token))

                    if exact_match:
                        logger.info(f"通过Drive API精确匹配到多维表格: {exact_match[0]} ({exact_match[1]})")
                        return {"app_token": exact_match[1], "tables": []}
                    elif len(partial_matches) == 1:
                        logger.info(f"通过Drive API模糊匹配到多维表格: {partial_matches[0][0]} ({partial_matches[0][1]})")
                        return {"app_token": partial_matches[0][1], "tables": []}
                    elif len(partial_matches) > 1:
                        names = [m[0] for m in partial_matches]
                        logger.warning(f"Drive API 模糊匹配到多个多维表格: {names}，使用第一个: {partial_matches[0][0]}")
                        return {"app_token": partial_matches[0][1], "tables": []}
                    else:
                        all_names = [f.get("name", "").lstrip("/") for f in files if f.get("token")]
                        logger.info(f"Drive API 搜索到 {len(all_names)} 个多维表格，但未匹配到 '{target}': {all_names}")
            else:
                logger.warning(f"Drive API 返回错误: code={search_result.get('code')}, msg={search_result.get('msg')}")
        except Exception as e:
            logger.warning(f"搜索多维表格失败: {e}")
    else:
        logger.warning("未提供 user_access_token，无法通过 Drive API 搜索")

    return None


async def analyze_form(
    target: str,
    user_access_token: str = "",
    send_to_user: str = "",
) -> dict:
    """分析多维表格数据

    Args:
        target: 多维表格目标（飞书URL 或 项目名）
        user_access_token: 用户访问令牌
        send_to_user: 接收报告的用户open_id（为空则不发送）
    """
    MAX_RECORDS_PER_TABLE = 50

    if not user_access_token:
        return {
            "ok": False,
            "error": "用户token无效或已过期，请使用 'auth login' 重新授权",
        }

    try:
        resolved_app_token = ""
        tables = []

        resolved = await resolve_bitable_target(target, user_access_token)
        if resolved:
            resolved_app_token = resolved.get("app_token", "")
            tables = resolved.get("tables", [])
            logger.info(f"已解析多维表格目标: app_token={resolved_app_token[:10]}..., tables_count={len(tables)}")

        if not resolved_app_token:
            return {
                "ok": False,
                "error": f"无法解析目标 '{target}' 对应的多维表格\n"
                         f"请使用：\n"
                         f"  1. 飞书多维表格URL（推荐，精确）：form https://xxx.feishu.cn/base/<app_token>?table=<table_id>\n"
                         f"  2. 项目名（Drive API模糊搜索）：form \"<项目名>\"",
            }

        if not feishu_client.is_configured:
            return {
                "ok": False,
                "error": "飞书客户端未配置，请检查服务端环境变量",
            }

        # 如果tables为空（多表模式），获取该文件下所有数据表
        if not tables:
            logger.info(f"多表模式：获取数据表列表")
            tables_result = await feishu_client.bitable_list_tables(
                app_token=resolved_app_token,
                user_access_token=user_access_token,
            )
            logger.debug(f"bitable_list_tables result: {tables_result}")

            if tables_result.get("code") == 0:
                table_items = tables_result.get("data", {}).get("items", [])
                if not table_items:
                    return {
                        "ok": False,
                        "error": f"多维表格 '{resolved_app_token}' 下没有数据表",
                    }
                tables = [{"table_id": t.get("table_id", ""), "table_name": t.get("name", "")} for t in table_items]
                logger.info(f"找到 {len(tables)} 个数据表: {[t.get('table_name') for t in tables]}")
            else:
                return {
                    "ok": False,
                    "error": f"获取数据表列表失败: {tables_result.get('msg', '未知错误')}",
                }

        # 遍历所有表，读取数据（每表最多 MAX_RECORDS_PER_TABLE 条）
        all_table_data = []
        total_records = 0
        read_errors = []

        for table_info in tables:
            table_id = table_info.get("table_id", "")
            table_name = table_info.get("table_name", f"table_{table_id}")

            if not table_id:
                continue

            result = await feishu_client.bitable_read_records(
                app_token=resolved_app_token,
                table_id=table_id,
                user_access_token=user_access_token,
                page_size=MAX_RECORDS_PER_TABLE,
            )

            logger.debug(f"bitable_read_records result for {table_name}: {result}")

            if result.get("error") or result.get("code") != 0:
                error_code = result.get("code", "unknown")
                error_msg = result.get("msg", result.get("error", "未知错误"))
                if error_code == 401 or "token" in str(error_msg).lower():
                    return {
                        "ok": False,
                        "error": "用户token已过期或无效，请使用 'auth login' 重新授权",
                    }
                elif error_code == 403:
                    return {
                        "ok": False,
                        "error": f"权限不足: 应用或用户没有访问数据表 '{table_name}' 的权限",
                    }
                read_errors.append(f"{table_name}: code={error_code}, {error_msg}")
                logger.warning(f"读取数据表 '{table_name}' 失败，跳过: {error_msg}")
                continue

            records = result.get("data", {}).get("items") or []
            actual_count = len(records)

            if actual_count == 0:
                read_errors.append(f"{table_name}: 无数据记录")
                logger.info(f"数据表 '{table_name}' 无数据")
                continue

            truncated_count = min(actual_count, MAX_RECORDS_PER_TABLE)
            if truncated_count < actual_count:
                logger.info(f"数据表 '{table_name}' 记录数({actual_count})超过上限({MAX_RECORDS_PER_TABLE})，已截断")

            all_table_data.append({
                "table_name": table_name,
                "table_id": table_id,
                "record_count": truncated_count,
                "total_available": actual_count,
                "records": records[:MAX_RECORDS_PER_TABLE],
            })
            total_records += truncated_count

        if not all_table_data:
            error_details = "\n".join([f"  - {e}" for e in read_errors]) if read_errors else ""
            return {
                "ok": False,
                "error": f"未读取到任何数据表数据 (app_token={resolved_app_token})\n{error_details}",
            }

        logger.info(f"多表数据读取完成: {len(all_table_data)} 个数据表，共 {total_records} 条记录")

        # 2. AI分析数据（多表模式）
        today = datetime.now().strftime("%Y-%m-%d")
        prompt = FORM_ANALYSIS_PROMPT.replace("{data}", str(all_table_data)).replace("{today}", today)
        ai_response = await llm_engine.generate(prompt)
        parsed = parse_ai_json(ai_response)

        if not parsed:
            return {
                "ok": False,
                "error": "AI无法生成分析报告",
                "raw_response": ai_response,
            }

        # 3. 通过机器人发送分析报告给用户
        sent_message = False
        if send_to_user and feishu_client.is_configured:
            summary_text = parsed.get("summary", {})
            insights = parsed.get("insights", [])
            recommendations = parsed.get("recommendations", [])

            insights_text = "\n".join([f"- {i}" for i in insights[:3]]) if insights else "无"
            if len(insights) > 3:
                insights_text += f"\n- ... 还有 {len(insights) - 3} 条洞察"

            recommendations_text = "\n".join([f"- {r}" for r in recommendations[:3]]) if recommendations else "无"
            if len(recommendations) > 3:
                recommendations_text += f"\n- ... 还有 {len(recommendations) - 3} 条建议"

            msg_content = {
                "text": f"📊 表单分析报告已生成！\n\n"
                        f"**分析范围**：{len(all_table_data)} 个数据表，共 {summary_text.get('total_records', total_records)} 条记录\n"
                        f"**平均评分**：{summary_text.get('average_value', '-')}\n\n"
                        f"**关键洞察**：\n{insights_text}\n\n"
                        f"**建议**：\n{recommendations_text}",
            }

            send_result = await feishu_client.send_message(
                receive_id=send_to_user,
                content=json.dumps(msg_content),
                msg_type="text",
                receive_id_type="open_id",
                use_app_token=True,
            )

            if send_result.get("code") == 0:
                sent_message = True
                logger.info(f"分析报告已发送给用户: {send_to_user}")
            else:
                logger.warning(f"发送消息失败: {send_result}")

        return {
            **parsed,
            "ok": True,
            "message": "表单分析完成",
            "target": target,
            "app_token": resolved_app_token,
            "tables": tables,
            "record_count": total_records,
            "sent_message": sent_message,
        }

    except Exception as e:
        logger.error(f"分析表单失败: {e}")
        return {"ok": False, "error": str(e)}
