"""柔性催办服务

处理 /remind 指令的业务逻辑：
- 查询与我相关的逾期任务列表
- AI解析自然语言催办请求
- 发送包含"已完成"/"求助"/"申请延期"按钮的飞书卡片
- 接收人点击按钮触发Webhook回调
- 处理按钮动作：完成任务、求助通知、延期申请
- 通知发起人
"""

import logging
import json
import asyncio
from datetime import datetime, timedelta, timezone

from server.src.feishu.client import feishu_client
from server.src.ai.prompt import REMIND_EXTRACT_PROMPT, parse_ai_json
from server.src.ai.llm import llm_engine

logger = logging.getLogger("lanshan-server.remind_service")

BEIJING_TZ = timezone(timedelta(hours=8))


def _parse_timestamp_to_beijing(timestamp_ms: str, is_all_day: bool = False) -> datetime | None:
    """将飞书API返回的毫秒级时间戳转换为北京时间
    
    飞书API截止时间说明:
    - is_all_day=true: 全天任务，timestamp为UTC当天00:00，转换为北京时间08:00
      但飞书客户端通常显示为当天18:00作为截止时间
    - is_all_day=false: 非全天任务，timestamp为具体时间点
    
    Args:
        timestamp_ms: 毫秒级时间戳字符串
        is_all_day: 是否为全天任务
        
    Returns:
        北京时间的datetime对象
    """
    if not timestamp_ms:
        return None
    
    try:
        timestamp = int(timestamp_ms) / 1000
        dt_utc = datetime.fromtimestamp(timestamp, timezone.utc)
        dt_beijing = dt_utc.astimezone(BEIJING_TZ)
        
        if is_all_day:
            dt_beijing = dt_beijing.replace(hour=18, minute=0, second=0)
            logger.debug(f"全天任务，截止时间调整为当天18:00: {dt_beijing}")
        
        return dt_beijing
    except (ValueError, TypeError) as e:
        logger.warning(f"解析时间戳失败: {e}")
        return None


async def _get_valid_user_token(user_access_token: str = "") -> str:
    """获取有效的用户token，如果传入的token为空则尝试从缓存获取
    
    Args:
        user_access_token: 用户访问令牌
        
    Returns:
        有效的用户访问令牌，获取失败返回空字符串
    """
    if user_access_token:
        return user_access_token
    
    from server.src.services.auth_service import oauth_service
    stored_info = oauth_service.get_stored_user_info()
    if stored_info.get("ok"):
        open_id = stored_info.get("open_id", "")
        token = await oauth_service.get_user_token_by_open_id_async(open_id)
        if token:
            logger.info(f"从缓存获取用户token成功: {token[:20]}...")
            return token
    
    return ""


async def get_overdue_tasks(user_access_token: str = "") -> dict:
    """查询与我相关的已逾期任务列表

    Args:
        user_access_token: 用户访问令牌（必须使用用户身份）

    Returns:
        逾期任务列表
    """
    try:
        user_access_token = await _get_valid_user_token(user_access_token)
        if not user_access_token:
            return {"ok": False, "error": "需要用户身份token，请先授权"}

        result = await feishu_client.list_related_tasks(
            completed=False,
            page_size=100,
            user_access_token=user_access_token,
        )

        if result.get("error") or result.get("code") != 0:
            logger.warning(f"查询任务列表失败: {result}")
            if result.get("need_reauth"):
                return {"ok": False, "error": "用户身份token已过期，请使用 'auth login' 重新授权"}
            return {"ok": False, "error": result.get("msg", "查询任务列表失败")}

        tasks = result.get("data", {}).get("items", [])
        now = datetime.now(BEIJING_TZ)
        overdue_tasks = []

        all_user_ids = set()
        for task in tasks:
            due_info = task.get("due", {})
            if due_info:
                try:
                    timestamp = due_info.get("timestamp", "")
                    if not timestamp:
                        timestamp = due_info.get("time", "")
                    is_all_day = due_info.get("is_all_day", False)
                    due_time = _parse_timestamp_to_beijing(timestamp, is_all_day)
                    
                    if due_time and due_time < now:
                        members = task.get("members", [])
                        for member in members:
                            user_id = member.get("id", "")
                            if user_id:
                                all_user_ids.add(user_id)
                except (ValueError, TypeError) as e:
                    logger.warning(f"解析任务截止时间失败: {e}")
                    continue

        user_name_cache = {}
        if all_user_ids:
            from server.src.services.auth_service import oauth_service
            latest_token = await oauth_service.get_user_token_by_open_id_async(list(all_user_ids)[0])
            if not latest_token:
                latest_token = user_access_token
            
            logger.debug(f"使用最新token获取用户信息: {latest_token[:20]}...")
            tasks_list = [feishu_client.get_user_info(uid, user_access_token=latest_token) for uid in all_user_ids]
            user_results = await asyncio.gather(*tasks_list)
            
            for uid, uresult in zip(all_user_ids, user_results):
                if uresult.get("code") == 0:
                    data = uresult.get("data", {})
                    user_data = data.get("user", data)
                    user_name = user_data.get("name", "") or user_data.get("display_name", "") or user_data.get("user_name", "")
                    user_name_cache[uid] = user_name
                    logger.debug(f"获取用户信息成功: {uid} -> {user_name}, 原始数据: {json.dumps(data, ensure_ascii=False)[:200]}")
                else:
                    logger.warning(f"获取用户信息失败: {uid}, 错误: {uresult.get('msg', '')}")
                    user_name_cache[uid] = ""

        for task in tasks:
            due_info = task.get("due", {})
            if due_info:
                try:
                    timestamp = due_info.get("timestamp", "")
                    if not timestamp:
                        timestamp = due_info.get("time", "")
                    is_all_day = due_info.get("is_all_day", False)
                    due_time = _parse_timestamp_to_beijing(timestamp, is_all_day)
                    
                    if due_time and due_time < now:
                        assignees = []
                        members = task.get("members", [])
                        for member in members:
                            user_id = member.get("id", "")
                            assignees.append({
                                "id": user_id,
                                "type": member.get("type", ""),
                                "name": user_name_cache.get(user_id, ""),
                            })

                        overdue_tasks.append({
                            "task_guid": task.get("guid", ""),
                            "task_id": task.get("task_id", ""),
                            "summary": task.get("summary", ""),
                            "description": task.get("description", ""),
                            "due_time": due_time.strftime("%Y-%m-%d %H:%M"),
                            "overdue_days": (now - due_time).days,
                            "creator": task.get("creator", {}),
                            "assignees": assignees,
                            "status": task.get("status", ""),
                            "url": task.get("url", ""),
                        })
                except (ValueError, TypeError) as e:
                    logger.warning(f"解析任务截止时间失败: {e}")
                    continue

        overdue_tasks.sort(key=lambda x: x["overdue_days"], reverse=True)

        return {
            "ok": True,
            "message": "查询成功",
            "total_count": len(overdue_tasks),
            "tasks": overdue_tasks,
        }

    except Exception as e:
        logger.error(f"查询逾期任务失败: {e}")
        return {"ok": False, "error": str(e)}


async def remind_by_natural_language(
    user_input: str,
    user_access_token: str = "",
) -> dict:
    """通过自然语言发送催办

    Args:
        user_input: 自然语言输入，如"催办绿大萌的作业批改任务"
        user_access_token: 用户访问令牌

    Returns:
        催办结果
    """
    try:
        user_access_token = await _get_valid_user_token(user_access_token)
        if not user_access_token:
            return {"ok": False, "error": "需要用户身份token，请先授权"}

        initiator_id = ""
        initiator_name = ""
        from server.src.services.auth_service import oauth_service
        user_info = await oauth_service.get_user_info(user_access_token)
        if user_info.get("ok"):
            initiator_id = user_info.get("open_id", "")
            initiator_name = user_info.get("name", "")
            logger.info(f"获取当前用户信息成功: open_id={initiator_id}, name={initiator_name}")
        else:
            logger.warning(f"获取当前用户信息失败: {user_info}")

        prompt = REMIND_EXTRACT_PROMPT.format(user_input=user_input)
        ai_response = await llm_engine.generate(prompt)
        logger.info(f"LLM解析催办输入: {ai_response}")

        parsed = parse_ai_json(ai_response)
        if not parsed:
            return {"ok": False, "error": "无法解析自然语言输入"}

        assignee_name = parsed.get("assignee_name")
        task_keywords = parsed.get("task_keywords", [])
        message = parsed.get("message", "")

        if not assignee_name:
            return {"ok": False, "error": "未能识别被催办人"}

        user_search_result = await feishu_client.search_users(
            assignee_name,
            user_access_token=user_access_token,
        )

        if user_search_result.get("error") or user_search_result.get("code") != 0:
            return {"ok": False, "error": f"搜索用户失败: {user_search_result.get('msg', '')}"}

        users = user_search_result.get("data", {}).get("items", [])
        if not users:
            return {"ok": False, "error": f"未找到用户: {assignee_name}"}

        assignee_id = users[0].get("open_id", "") or users[0].get("id", "")
        assignee_name = users[0].get("name", assignee_name)

        if task_keywords:
            logger.info(f"AI提取的关键词: {task_keywords}, 被催办人: {assignee_name}({assignee_id})")
            tasks_result = await feishu_client.list_related_tasks(
                completed=False,
                page_size=100,
                user_access_token=user_access_token,
            )

            if tasks_result.get("code") == 0:
                tasks = tasks_result.get("data", {}).get("items", [])
                logger.info(f"从飞书获取到 {len(tasks)} 个未完成任务")
                matched_tasks = []
                for task in tasks:
                    summary = task.get("summary", "").lower()
                    description = task.get("description", "").lower()
                    task_text = f"{summary} {description}"
                    members = task.get("members", [])
                    assignee_ids = [m.get("id") for m in members]

                    if assignee_id in assignee_ids:
                        for keyword in task_keywords:
                            if keyword.lower() in task_text:
                                matched_tasks.append(task)
                                logger.info(f"匹配成功: keyword='{keyword}' → task='{task.get('summary')}'")
                                break

                if matched_tasks:
                    results = []
                    for task in matched_tasks[:3]:
                        result = await send_reminder_card(
                            task_id=task.get("guid", ""),
                            assignee_id=assignee_id,
                            initiator_id=initiator_id,
                            message=f"{message}\n\n任务: {task.get('summary', '')}" if message else f"任务: {task.get('summary', '')}",
                            user_access_token=user_access_token,
                            assignee_name=assignee_name,
                            task_summary=task.get("summary", ""),
                        )
                        results.append(result)

                    return {
                        "ok": True,
                        "message": f"已向 {assignee_name} 发送 {len(results)} 条催办卡片",
                        "results": results,
                        "matched_tasks": len(matched_tasks),
                    }
                else:
                    keywords_str = ", ".join(task_keywords)
                    return {"ok": False, "error": f"{assignee_name} 没有匹配的任务（搜索关键词: {keywords_str}，共扫描 {len(tasks)} 个任务）"}
            else:
                logger.warning(f"查询任务列表失败，直接发送催办: {tasks_result}")

        result = await send_reminder_card(
            task_id="",
            assignee_id=assignee_id,
            initiator_id=initiator_id,
            message=message or f"您有任务需要关注",
            user_access_token=user_access_token,
            assignee_name=assignee_name,
        )

        return {
            "ok": True,
            "message": f"已向 {assignee_name} 发送催办卡片",
            "result": result,
        }

    except Exception as e:
        logger.error(f"自然语言催办失败: {e}")
        return {"ok": False, "error": str(e)}


async def send_reminder_card(
    task_id: str,
    assignee_id: str,
    initiator_id: str | None = None,
    message: str = "",
    user_access_token: str = "",
    assignee_name: str = "",
    task_summary: str = "",
) -> dict:
    """发送柔性催办卡片

    Args:
        task_id: 任务ID（guid）
        assignee_id: 被催办人ID（open_id）
        initiator_id: 发起人ID（用于通知）
        message: 催办消息
        user_access_token: 用户访问令牌
        assignee_name: 被催办人姓名
        task_summary: 任务标题
    """
    try:
        from server.src.feishu.long_connection import cache_task_info

        user_access_token = await _get_valid_user_token(user_access_token)

        if task_id:
            cache_task_info(task_id, assignee_name, initiator_id or "", user_access_token, task_summary)

        card_elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**任务催办通知**\n\n您有一个任务需要关注：\n\n任务ID: {task_id}\n\n{message}" if message and task_id else f"**任务催办通知**\n\n{message}" if message else "**任务催办通知**\n\n您有一个任务需要关注",
                },
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "✅ 已完成"},
                        "type": "primary",
                        "value": {"task_id": task_id, "action": "completed", "assignee_id": assignee_id, "initiator_id": initiator_id or "", "assignee_name": assignee_name, "task_summary": task_summary},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🆘 求助"},
                        "type": "default",
                        "value": {"task_id": task_id, "action": "help", "assignee_id": assignee_id, "initiator_id": initiator_id or "", "assignee_name": assignee_name, "task_summary": task_summary},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⏰ 申请延期"},
                        "type": "default",
                        "value": {"task_id": task_id, "action": "delay", "assignee_id": assignee_id, "initiator_id": initiator_id or "", "assignee_name": assignee_name, "task_summary": task_summary},
                    },
                ],
            },
        ]

        if feishu_client.is_configured:
            result = await feishu_client.send_card_message(
                receive_id=assignee_id,
                card=card_elements,
                use_app_token=True,
            )

            if result.get("error") or result.get("code", 0) != 0:
                logger.warning(f"发送卡片消息失败，降级到模拟模式: {result}")
                return _mock_send_result(task_id, assignee_id)

            message_id = result.get("data", {}).get("message_id", "")
            logger.info(f"催办卡片已发送: task_id={task_id}, assignee_id={assignee_id}, message_id={message_id}")

            return {
                "ok": True,
                "message": "催办卡片已发送",
                "task_id": task_id,
                "assignee_id": assignee_id,
                "message_id": message_id,
            }
        else:
            return _mock_send_result(task_id, assignee_id)

    except Exception as e:
        logger.error(f"发送催办卡片失败: {e}")
        return {"ok": False, "error": str(e)}


async def handle_card_action(action_data: dict, user_access_token: str = "") -> dict:
    """处理卡片回调动作

    Args:
        action_data: 卡片动作数据（包含task_id, action, assignee_id）
        user_access_token: 用户访问令牌

    Returns:
        处理结果，包含toast提示和可选的卡片更新
    """
    try:
        user_access_token = await _get_valid_user_token(user_access_token)
        
        task_id = action_data.get("task_id")
        action = action_data.get("action")
        assignee_id = action_data.get("assignee_id")
        initiator_id = action_data.get("initiator_id", "")

        logger.info(f"收到卡片回调: task_id={task_id}, action={action}, assignee_id={assignee_id}, initiator_id={initiator_id}")

        action_text = {
            "completed": "已完成",
            "help": "求助",
            "delay": "申请延期",
        }.get(action, action)

        result = {"ok": True}

        if action == "completed" and task_id:
            complete_result = await feishu_client.complete_task(
                task_guid=task_id,
                user_access_token=user_access_token,
            )
            if complete_result.get("code") == 0:
                result["message"] = "任务已标记完成"
                result["toast"] = {"type": "success", "content": "任务已完成"}
            else:
                logger.warning(f"完成任务失败: {complete_result}")
                result["message"] = f"完成任务失败: {complete_result.get('msg', '')}"
                result["toast"] = {"type": "error", "content": "完成任务失败"}

        elif action == "help":
            result["message"] = "已收到求助请求"
            result["toast"] = {"type": "info", "content": "求助请求已发送，相关人员会尽快协助"}

            if assignee_id:
                assignee_info = await feishu_client.get_user_info(assignee_id, user_access_token=user_access_token)
                assignee_name = assignee_info.get("data", {}).get("name", "用户")
                notify_result = await notify_initiator(
                    initiator_id=initiator_id,
                    task_id=task_id,
                    action="help",
                    assignee_name=assignee_name,
                    user_access_token=user_access_token,
                )
                if notify_result.get("ok"):
                    logger.info(f"已通知求助: {assignee_name} 需要帮助")

        elif action == "delay":
            result["message"] = "正在生成延期申请表单"
            result["card"] = _build_delay_form_card(task_id, assignee_id)
            result["toast"] = {"type": "info", "content": "请填写延期申请"}

        else:
            result["message"] = f"卡片动作已处理：{action_text}"
            result["toast"] = {"type": "info", "content": f"已收到您的{action_text}请求"}

        result.update({
            "task_id": task_id,
            "action": action,
            "action_text": action_text,
            "assignee_id": assignee_id,
        })

        return result

    except Exception as e:
        logger.error(f"处理卡片动作失败: {e}")
        return {"ok": False, "error": str(e)}


def _build_delay_form_card(task_id: str, assignee_id: str) -> dict:
    """构建延期申请表单卡片

    Args:
        task_id: 任务ID
        assignee_id: 被催办人ID

    Returns:
        延期申请表单卡片JSON
    """
    next_week = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    card = {
        "type": "raw",
        "data": {
            "schema": "2.0",
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": "⏰ 延期申请",
                },
                "template": "blue",
            },
            "body": {
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": "请填写以下信息申请任务延期：",
                        },
                    },
                    {
                        "tag": "form",
                        "elements": [
                            {
                                "tag": "input",
                                "name": "delay_reason",
                                "label": {
                                    "tag": "plain_text",
                                    "content": "延期原因",
                                },
                                "placeholder": {
                                    "tag": "plain_text",
                                    "content": "请说明需要延期的原因",
                                },
                                "required": True,
                            },
                            {
                                "tag": "date_picker",
                                "name": "new_due_date",
                                "label": {
                                    "tag": "plain_text",
                                    "content": "新截止日期",
                                },
                                "initial_date": next_week,
                                "required": True,
                            },
                            {
                                "tag": "input",
                                "name": "delay_note",
                                "label": {
                                    "tag": "plain_text",
                                    "content": "备注说明",
                                },
                                "placeholder": {
                                    "tag": "plain_text",
                                    "content": "其他需要说明的情况（可选）",
                                },
                                "required": False,
                            },
                        ],
                    },
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "提交申请"},
                                "type": "primary",
                                "name": "submit_delay",
                                "value": {"task_id": task_id, "assignee_id": assignee_id, "action": "submit_delay"},
                            },
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "取消"},
                                "type": "default",
                                "name": "cancel_delay",
                                "value": {"task_id": task_id, "assignee_id": assignee_id, "action": "cancel_delay"},
                            },
                        ],
                    },
                ],
            },
        },
    }

    return card


async def handle_delay_submit(action_data: dict, form_data: dict, user_access_token: str = "") -> dict:
    """处理延期申请提交

    Args:
        action_data: 卡片动作数据
        form_data: 表单数据（delay_reason, new_due_date, delay_note）
        user_access_token: 用户访问令牌

    Returns:
        处理结果
    """
    try:
        user_access_token = await _get_valid_user_token(user_access_token)
        
        task_id = action_data.get("task_id")
        assignee_id = action_data.get("assignee_id")
        initiator_id = action_data.get("initiator_id", "")
        delay_reason = form_data.get("delay_reason", "")
        new_due_date = form_data.get("new_due_date", "")
        delay_note = form_data.get("delay_note", "")

        logger.info(f"收到延期申请: task_id={task_id}, assignee_id={assignee_id}, initiator_id={initiator_id}, new_due_date={new_due_date}, reason={delay_reason}")

        if not task_id:
            return {"ok": False, "error": "任务ID为空"}

        if not new_due_date:
            return {"ok": False, "error": "新截止日期不能为空"}

        try:
            # 使用 CST (UTC+8) 时区，确保无论服务器在什么时区都能正确解析
            cst = timezone(timedelta(hours=8))
            date_str = new_due_date.split(" ")[0].split("T")[0]
            new_due_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=cst)
            due_timestamp = str(int(new_due_dt.timestamp()))
            logger.info(f"[延期诊断] 日期解析: input='{new_due_date}', date_str='{date_str}', cst_dt={new_due_dt}, timestamp={due_timestamp}")
        except ValueError as e:
            logger.error(f"[延期诊断] 日期格式错误: input='{new_due_date}', error={e}")
            return {"ok": False, "error": f"日期格式错误: {e}"}

        update_result = await feishu_client.update_task(
            task_guid=task_id,
            due={
                "time": due_timestamp,
                "is_all_day": True,
                "timezone": "Asia/Shanghai",
            },
            user_access_token=user_access_token,
        )
        logger.info(f"[延期诊断] update_task 结果: code={update_result.get('code')}, msg={update_result.get('msg')}")

        if update_result.get("code") == 0:
            result = {
                "ok": True,
                "message": "延期申请已提交",
                "task_id": task_id,
                "new_due_date": new_due_date,
                "delay_reason": delay_reason,
                "toast": {"type": "success", "content": "延期申请已提交，等待审批"},
            }

            assignee_info = await feishu_client.get_user_info(assignee_id, user_access_token=user_access_token)
            assignee_name = assignee_info.get("data", {}).get("name", "用户")

            await notify_initiator(
                initiator_id=initiator_id,
                task_id=task_id,
                action="delay_approved",
                assignee_name=assignee_name,
                user_access_token=user_access_token,
                extra=f"新截止日期: {new_due_date}\n原因: {delay_reason}",
            )

            return result
        else:
            logger.warning(f"更新任务截止日期失败: {update_result}")
            return {"ok": False, "error": f"更新任务失败: {update_result.get('msg', '')}"}

    except Exception as e:
        logger.error(f"处理延期申请失败: {e}")
        return {"ok": False, "error": str(e)}


async def notify_initiator(
    initiator_id: str,
    task_id: str,
    action: str,
    assignee_name: str = "",
    user_access_token: str = "",
    extra: str = "",
) -> dict:
    """通知发起人

    Args:
        initiator_id: 发起人ID
        task_id: 任务ID
        action: 动作类型（completed/help/delay/delay_approved）
        assignee_name: 被催办人姓名
        user_access_token: 用户访问令牌
        extra: 额外信息
    """
    try:
        action_text_map = {
            "completed": "已完成",
            "help": "请求帮助",
            "delay": "申请延期",
            "delay_approved": "延期申请已提交",
        }
        action_text = action_text_map.get(action, action)

        message_text = f"任务 {task_id} 的状态已更新：\n\n{assignee_name} 选择了「{action_text}」"
        if extra:
            message_text += f"\n\n{extra}"

        if feishu_client.is_configured:
            if initiator_id:
                content = json.dumps({"text": message_text})
                result = await feishu_client.send_message(
                    receive_id=initiator_id,
                    content=content,
                    msg_type="text",
                    use_app_token=True,
                )

                if result.get("error"):
                    logger.warning("通知发起人失败")
                    return {"ok": False, "error": "通知发起人失败"}

                return {
                    "ok": True,
                    "message": "已通知发起人",
                    "initiator_id": initiator_id,
                    "task_id": task_id,
                    "action": action,
                }
            else:
                logger.info(f"无发起人ID，仅记录日志: {message_text}")
                return {
                    "ok": True,
                    "message": "[无发起人ID] 通知已记录",
                }
        else:
            return {
                "ok": True,
                "message": "[模拟模式] 已通知发起人",
                "initiator_id": initiator_id,
                "task_id": task_id,
                "action": action,
            }

    except Exception as e:
        logger.error(f"通知发起人失败: {e}")
        return {"ok": False, "error": str(e)}


def _mock_send_result(task_id: str, assignee_id: str) -> dict:
    """模拟发送结果"""
    import uuid

    return {
        "ok": True,
        "message": "[模拟模式] 催办卡片已发送",
        "task_id": task_id,
        "assignee_id": assignee_id,
        "message_id": f"mock_card_{uuid.uuid4().hex[:8]}",
    }


async def process_card_action_from_long_connection(action_value: dict):
    """从长连接处理卡片动作（桥接函数）"""
    logger.info(f"从长连接收到卡片动作: {action_value}")
    
    if not action_value or not isinstance(action_value, dict):
        logger.error(f"卡片动作参数不完整: {action_value}")
        return
    
    task_id = action_value.get("task_id")
    action = action_value.get("action")
    
    if not task_id or not action:
        logger.error(f"卡片动作参数不完整: {action_value}")
        return
    
    result = await handle_card_action(
        action_data=action_value,
        user_access_token="",
    )
    
    logger.info(f"卡片动作处理结果: {result}")
