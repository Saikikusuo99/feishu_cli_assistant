"""待办任务服务

处理 /todo 指令的业务逻辑：AI解析用户输入 → 创建飞书任务 → 记录到数据库。
"""

import logging
from datetime import datetime, timedelta
import calendar
import re
import json

from server.src.feishu.client import feishu_client
from server.src.core.config import server_config
from server.src.ai.llm import llm_engine
from server.src.ai.prompt import TASK_EXTRACT_PROMPT, parse_ai_json
from server.src.services.auth_service import oauth_service


def _parse_chinese_time(user_input: str) -> str | None:
    """解析中文时间表达
    
    支持的时间表达：
    - 下午6点、下午6:00
    - 上午9点、上午9:30
    - 晚上8点、晚上20:00
    - 14:00、14点
    
    Args:
        user_input: 用户原始输入文本
    
    Returns:
        HH:MM格式的时间字符串，无法解析返回None
    """
    patterns = [
        (r"下午(\d{1,2})[:点]?(\d{0,2})?", lambda h, m: f"{int(h)+12:02d}:{m.zfill(2) if m else '00'}"),
        (r"上午(\d{1,2})[:点]?(\d{0,2})?", lambda h, m: f"{int(h):02d}:{m.zfill(2) if m else '00'}"),
        (r"晚上(\d{1,2})[:点]?(\d{0,2})?", lambda h, m: f"{int(h)+12 if int(h) < 12 else int(h):02d}:{m.zfill(2) if m else '00'}"),
        (r"(\d{1,2}):(\d{2})", lambda h, m: f"{int(h):02d}:{int(m):02d}"),
        (r"(\d{1,2})点(\d{0,2})?", lambda h, m: f"{int(h):02d}:{m.zfill(2) if m else '00'}"),
    ]
    
    for pattern, formatter in patterns:
        match = re.search(pattern, user_input)
        if match:
            h = match.group(1)
            m = match.group(2) if len(match.groups()) > 1 else ""
            try:
                return formatter(h, m)
            except ValueError:
                pass
    
    return None


def _parse_chinese_date(user_input: str) -> str | None:
    """解析中文日期表达
    
    支持的日期表达：
    - 今天、明天、后天、昨天
    - 本周、下周、上周
    - 本月、下月、上月
    - 下周一、下周二...下周日
    - 本周一、本周二...本周日
    - 下周三下午（会提取"下周三"）
    
    Args:
        user_input: 用户原始输入文本
    
    Returns:
        YYYY-MM-DD格式的日期字符串，无法解析返回None
    """
    today = datetime.now().date()
    week_days = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6}
    
    # 预计算月末日期
    _, last_day = calendar.monthrange(today.year, today.month)
    end_of_this_month = today.replace(day=last_day)
    next_month_first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    _, next_last_day = calendar.monthrange(next_month_first.year, next_month_first.month)
    end_of_next_month = next_month_first.replace(day=next_last_day)
    prev_month_last = today.replace(day=1) - timedelta(days=1)
    _, prev_last_day = calendar.monthrange(prev_month_last.year, prev_month_last.month)
    end_of_prev_month = prev_month_last.replace(day=prev_last_day)
    
    patterns = [
        (r"今天", today),
        (r"明天", today + timedelta(days=1)),
        (r"后天", today + timedelta(days=2)),
        (r"昨天", today - timedelta(days=1)),
        (r"本周", today + timedelta(days=(6 - today.weekday()))),
        (r"下周", today + timedelta(days=(7 - today.weekday()) + 6)),
        (r"上周", today - timedelta(days=today.weekday() + 1)),
        (r"本月", end_of_this_month),
        (r"下月", end_of_next_month),
        (r"上月", end_of_prev_month),
    ]
    
    for pattern, default_date in patterns:
        if pattern in user_input:
            return default_date.strftime("%Y-%m-%d")
    
    match = re.search(r"(下|本)(周)?(一|二|三|四|五|六|日)", user_input)
    if match:
        prefix = match.group(1)
        day_char = match.group(3)
        target_weekday = week_days.get(day_char)
        
        if target_weekday is not None:
            days_ahead = target_weekday - today.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            
            if prefix == "本":
                if days_ahead > 0:
                    result_date = today + timedelta(days=days_ahead)
                else:
                    result_date = today
            else:
                result_date = today + timedelta(days=days_ahead)
            
            return result_date.strftime("%Y-%m-%d")
    
    date_match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?", user_input)
    if date_match:
        try:
            year = int(date_match.group(1))
            month = int(date_match.group(2))
            day = int(date_match.group(3))
            return f"{year:04d}-{month:02d}-{day:02d}"
        except ValueError:
            pass
    
    relative_month_match = re.search(r"(下|本|上)(个)?月(\d{1,2})[号日]?", user_input)
    if relative_month_match:
        try:
            prefix = relative_month_match.group(1)
            day = int(relative_month_match.group(3))
            
            if prefix == "下":
                next_month_first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
                year = next_month_first.year
                month = next_month_first.month
            elif prefix == "上":
                prev_month_last = today.replace(day=1) - timedelta(days=1)
                year = prev_month_last.year
                month = prev_month_last.month
            else:
                year = today.year
                month = today.month
            
            return f"{year:04d}-{month:02d}-{day:02d}"
        except ValueError:
            pass

    date_match_no_year = re.search(r"(\d{1,2})月(\d{1,2})[号日]?", user_input)
    if date_match_no_year:
        try:
            month = int(date_match_no_year.group(1))
            day = int(date_match_no_year.group(2))
            year = today.year
            if month < today.month or (month == today.month and day < today.day):
                year += 1
            return f"{year:04d}-{month:02d}-{day:02d}"
        except ValueError:
            pass
    
    return None


logger = logging.getLogger("lanshan-server.todo_service")


async def create_todo(user_input: str, user_open_id: str = "", user_access_token: str = "", sync_chat: str = "") -> dict:
    """创建待办任务

    使用AI从用户自然语言输入中提取任务信息，
    然后调用飞书API创建任务。

    Args:
        user_input: 用户原始输入，如 "完成周报 @张三 明天"
        user_open_id: 发起人 open_id
        user_access_token: 用户访问令牌（优先使用用户身份）
        sync_chat: 强制指定要同步的群聊名称（可选，AI解析优先）
    """
    try:
        # 1. AI 解析输入
        prompt = TASK_EXTRACT_PROMPT.format(
            today=datetime.now().strftime("%Y-%m-%d"),
            user_input=user_input,
        )
        ai_response = await llm_engine.generate(prompt)
        logger.info(f"LLM原始响应: {ai_response}")
        parsed = parse_ai_json(ai_response)

        content = user_input
        due_date = None
        due_time = None
        priority = None
        mentions = []
        parsed_sync_chat = None

        if parsed:
            ai_content = parsed.get("content")
            if ai_content and ai_content not in (None, "null", "None", ""):
                content = ai_content
            
            ai_due_date = parsed.get("due_date")
            if ai_due_date and ai_due_date not in (None, "null", "None", ""):
                due_date = ai_due_date
            
            ai_priority = parsed.get("priority")
            if ai_priority and ai_priority not in (None, "null", "None", ""):
                priority = ai_priority
            
            ai_mentions = parsed.get("mentions", [])
            if ai_mentions and isinstance(ai_mentions, list):
                mentions = [m for m in ai_mentions if m]
            
            ai_sync_chat = parsed.get("sync_chat")
            if ai_sync_chat and ai_sync_chat not in (None, "null", "None", ""):
                parsed_sync_chat = ai_sync_chat
                sync_chat = parsed_sync_chat or sync_chat

            ai_due_time = parsed.get("time")
            if ai_due_time and ai_due_time not in (None, "null", "None", ""):
                due_time = ai_due_time

        if not due_date:
            due_date = _parse_chinese_date(user_input)
            if due_date:
                logger.info(f"代码层面日期解析成功: {due_date}")
        else:
            try:
                parsed_date = datetime.strptime(due_date, "%Y-%m-%d").date()
                today = datetime.now().date()
                if parsed_date < today - timedelta(days=30):
                    logger.warning(f"LLM返回的日期 {due_date} 过于久远，使用代码层面解析")
                    code_date = _parse_chinese_date(user_input)
                    if code_date:
                        due_date = code_date
            except ValueError:
                code_date = _parse_chinese_date(user_input)
                if code_date:
                    due_date = code_date

        if not due_time:
            due_time = _parse_chinese_time(user_input)
            if due_time:
                logger.info(f"代码层面时间解析成功: {due_time}")

        logger.info(f"最终解析结果: content={content}, due_date={due_date}, due_time={due_time}, mentions={mentions}")

        # 2. 先确保用户token可用（自动刷新过期token）
        if not user_access_token and user_open_id:
            server_token = await oauth_service.get_user_token_by_open_id_async(user_open_id)
            if server_token:
                user_access_token = server_token
                logger.info(f"从服务端存储获取到用户token（支持自动续期）")
            else:
                logger.error(f"服务端无有效用户token，请先进行OAuth授权")
                return {
                    "ok": False,
                    "error": "用户身份认证失败，请先通过授权链接进行飞书登录授权",
                    "auth_url": oauth_service.get_auth_url(),
                }

        # 3. 如果有 @成员，查找 open_id（所有 @人 都添加为协作人）
        # 所有被 @的成员和创建者本人都会成为协作人
        collaborator_ids = [user_open_id] if user_open_id else []
        
        current_user_name = ""
        if user_access_token and user_open_id:
            try:
                user_info = await oauth_service.get_user_info(user_access_token)
                if user_info.get("ok"):
                    current_user_name = user_info.get("name", "")
                    logger.info(f"当前用户信息: name={current_user_name}, open_id={user_open_id}")
            except Exception as e:
                logger.warning(f"获取当前用户信息失败: {e}")

        if mentions and feishu_client.is_configured:
            for member_name in mentions:
                found_id = None
                
                if current_user_name and (member_name == current_user_name or 
                                     member_name == current_user_name.split()[0] or
                                     current_user_name in member_name):
                    found_id = user_open_id
                    logger.info(f"@提及的是当前用户 {member_name}（匹配 {current_user_name}），直接使用 user_open_id: {found_id}")
                else:
                        mapped_id = server_config.member_mapping.get(member_name)
                        if mapped_id:
                            found_id = mapped_id
                            logger.info(f"从映射表找到成员: {member_name} -> {found_id}")
                        else:
                            if user_access_token:
                                try:
                                    search_result = await feishu_client.search_users(member_name, user_access_token=user_access_token)
                                    users = search_result.get("data", {}).get("items", [])
                                    if users:
                                        found_id = users[0].get("open_id")
                                        logger.info(f"找到成员: {member_name} -> {found_id}")
                                    else:
                                        logger.warning(f"未找到成员: {member_name}")
                                except Exception as e:
                                    logger.warning(f"搜索成员失败: {e}")
                            else:
                                logger.warning(f"无用户身份token，无法搜索成员: {member_name}")
                
                if found_id and found_id not in collaborator_ids:
                    collaborator_ids.append(found_id)
                    logger.info(f"加入协作人: {member_name} -> {found_id}")

        logger.info(f"协作人列表: {collaborator_ids}")

        # 4. 调用飞书 API 创建任务
        if feishu_client.is_configured:
            description = ""
            if priority:
                description += f"优先级: {priority}\n"
            if mentions:
                description += f"参与人: {', '.join(mentions)}"

            logger.info(f"创建任务: content={content}, user_open_id={user_open_id}, mentions={mentions}, collaborators={collaborator_ids}")

            # 用用户身份创建任务
            result = await feishu_client.create_task(
                summary=content,
                due_date=due_date,
                due_time=due_time,
                description=description or None,
                user_access_token=user_access_token,
                assignee_ids=collaborator_ids if collaborator_ids else None,
            )
            
            logger.info(f"创建任务结果: code={result.get('code')}, task_id={result.get('data', {}).get('task', {}).get('id')}")

            # 飞书API调用失败，返回具体错误
            if result.get("error"):
                error_code = result.get("code", -1)
                error_msg = result.get("msg", "未知错误")

                # 飞书业务错误码映射
                FEISHU_ERROR_MAP = {
                    99991679: "用户授权权限不足，缺少 task:task 权限。请重新授权：client> auth login（确保飞书应用已配置 task:task 权限）",
                    99991663: "用户token无效，请重新登录：client> auth login",
                    99991664: "用户token已过期，请重新登录：client> auth login",
                    230001: "任务不存在或已被删除",
                    230002: "任务已过期",
                }

                if error_code in FEISHU_ERROR_MAP:
                    message = FEISHU_ERROR_MAP[error_code]
                elif error_code == 401:
                    if result.get("need_reauth"):
                        message = "用户授权已过期，请重新登录：client> auth login"
                    else:
                        message = f"飞书API认证失败（401），请检查应用凭证配置"
                elif error_code == 400:
                    message = f"请求参数错误（400）：{error_msg}"
                elif error_code == 403:
                    message = f"没有操作权限（403），请检查飞书应用是否已开通 task:task 权限"
                elif error_code == 404:
                    message = f"飞书API接口不存在（404），请检查API路径"
                elif error_code == 429:
                    message = f"请求过于频繁（429），请稍后重试"
                elif error_code >= 500:
                    message = f"飞书服务端错误（{error_code}），请稍后重试或联系飞书支持"
                elif error_code == -1:
                    message = f"网络连接失败，请检查网络或服务端是否正常运行"
                else:
                    message = f"飞书API返回错误（{error_code}）：{error_msg}"

                logger.warning(f"飞书API创建任务失败: {message}")
                return {
                    "ok": False,
                    "error": message,
                    "content": content,
                    "due_date": due_date,
                    "due_time": due_time,
                    "mentions": mentions,
                    "priority": priority,
                    "sync_chat": sync_chat,
                }

            task_data = result.get("data", {}).get("task", {})
            task_id = task_data.get("id", task_data.get("guid", ""))

            for cid in collaborator_ids:
                try:
                    # 用用户身份添加协作人
                    collab_result = await feishu_client.add_task_collaborator(
                        task_id=task_id,
                        user_id=cid,
                        user_access_token=user_access_token,
                    )
                    if collab_result.get("code") == 0:
                        logger.info(f"成功添加协作人: {cid}")
                    else:
                        logger.warning(f"添加协作人失败: {collab_result}")
                except Exception as e:
                    logger.warning(f"添加协作人异常: {e}")

            chat_message_sent = False
            chat_id = ""
            if sync_chat and feishu_client.is_configured:
                try:
                    chat_id = server_config.chat_mapping.get(sync_chat)
                    if not chat_id and sync_chat.endswith("群"):
                        chat_id = server_config.chat_mapping.get(sync_chat[:-1])
                    
                    if chat_id:
                        logger.info(f"从配置文件找到群聊: {sync_chat} -> {chat_id}")
                    else:
                        search_result = await feishu_client.search_chats(
                            keyword=sync_chat,
                            user_access_token=user_access_token,
                        )
                        if search_result.get("code") == 0:
                            all_chats = search_result.get("data", {}).get("items", [])
                            logger.info(f"获取群聊列表: 共 {len(all_chats)} 个群聊")
                            
                            for chat in all_chats:
                                chat_name = chat.get("name", "")
                                logger.debug(f"群聊: {chat_name}")
                                if sync_chat in chat_name or chat_name in sync_chat:
                                    chat_id = chat.get("chat_id", "")
                                    logger.info(f"匹配到群聊: {chat_name} -> {chat_id}")
                                    break
                            
                            if not chat_id:
                                for chat in all_chats:
                                    chat_name = chat.get("name", "")
                                    sync_chat_clean = sync_chat.replace("群", "").replace("组", "").strip()
                                    chat_name_clean = chat_name.replace("群", "").replace("组", "").strip()
                                    if sync_chat_clean in chat_name_clean or chat_name_clean in sync_chat_clean:
                                        chat_id = chat.get("chat_id", "")
                                        logger.info(f"模糊匹配到群聊: {sync_chat} -> {chat_name} -> {chat_id}")
                                        break

                    if chat_id:
                        task_url = task_data.get("url", "")
                        mention_text = f" @{' @'.join(mentions)}" if mentions else ""
                        message_text = f"📋 新任务已创建{mention_text}\n\n任务内容: {content}\n截止日期: {due_date or '未设置'}\n优先级: {priority or '未设置'}\n{task_url}"
                        send_result = await feishu_client.send_message(
                            receive_id=chat_id,
                            content=json.dumps({"text": message_text}),
                            msg_type="text",
                            receive_id_type="chat_id",
                        )
                        if send_result.get("code") == 0:
                            chat_message_sent = True
                            logger.info(f"任务已同步到群聊 {sync_chat} ({chat_id})")
                        else:
                            logger.warning(f"发送群消息失败: {send_result}")
                    else:
                        logger.warning(f"未找到群聊: {sync_chat}。应用身份无权限获取群聊列表，请使用群聊ID或在配置文件中指定。")
                except Exception as e:
                    logger.warning(f"同步群聊异常: {e}")

            return {
                "ok": True,
                "message": "飞书任务创建成功" + ("，已同步到群聊" if chat_message_sent else ""),
                "task_id": task_id,
                "task_url": task_data.get("url", ""),
                "content": content,
                "due_date": due_date,
                "due_time": due_time,
                "mentions": mentions,
                "priority": priority,
                "sync_chat": sync_chat,
                "chat_message_sent": chat_message_sent,
                "raw_parsed": parsed,
            }
        else:
            # 飞书未配置时返回解析结果
            return {
                "ok": True,
                "message": "[模拟模式] 任务信息已解析（飞书未配置）",
                "content": content,
                "due_date": due_date,
                "due_time": due_time,
                "mentions": mentions,
                "priority": priority,
                "sync_chat": sync_chat,
                "raw_parsed": parsed,
            }

    except Exception as e:
        logger.error(f"创建任务失败: {e}")
        return {"ok": False, "error": str(e)}
