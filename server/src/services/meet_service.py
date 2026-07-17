"""会议服务

处理 /meet 指令的业务逻辑：
- 创建会议
- 智能安排会议（查询忙闲 + 推荐时间 + 预订）
- 查询忙闲状态
- 搜索成员
"""

import logging
from datetime import datetime, timedelta

from server.src.feishu.client import feishu_client
from server.src.ai.llm import llm_engine
from server.src.ai.prompt import MEETING_SCHEDULE_PROMPT, parse_ai_json
from server.src.services.auth_service import oauth_service

logger = logging.getLogger("lanshan-server.meet_service")


async def _find_available_room(
    start_time: str,
    end_time: str,
    user_access_token: str,
) -> str | None:
    """查找可用的会议室

    Args:
        start_time: 开始时间 ISO格式
        end_time: 结束时间 ISO格式
        user_access_token: 用户访问令牌

    Returns:
        可用的会议室ID，无可用会议室返回None
    """
    try:
        rooms_result = await feishu_client.list_meeting_rooms(user_access_token=user_access_token)
        if rooms_result.get("error") or rooms_result.get("code") != 0:
            logger.warning(f"获取会议室列表失败: {rooms_result}")
            return None

        rooms = rooms_result.get("data", {}).get("items", [])
        if not rooms:
            logger.info("未找到会议室")
            return None

        room_ids = [room.get("resource_id", "") for room in rooms if room.get("resource_id")]
        if not room_ids:
            return None

        freebusy_result = await feishu_client.query_meeting_room_freebusy(
            room_ids=room_ids[:10],
            time_min=start_time,
            time_max=end_time,
            user_access_token=user_access_token,
        )

        if freebusy_result.get("error") or freebusy_result.get("code") != 0:
            logger.warning(f"查询会议室忙闲失败: {freebusy_result}")
            return None

        for room_id in room_ids[:5]:
            is_busy = False
            busy_list = freebusy_result.get("data", {}).get(room_id, [])
            for busy_slot in busy_list:
                slot_start = busy_slot.get("start_time", {}).get("timestamp", "")
                slot_end = busy_slot.get("end_time", {}).get("timestamp", "")
                if slot_start and slot_end:
                    start_ts = int(slot_start)
                    end_ts = int(slot_end)
                    if start_ts < int(end_time[:10]) and end_ts > int(start_time[:10]):
                        is_busy = True
                        break
            if not is_busy:
                logger.info(f"找到可用会议室: {room_id}")
                return room_id

        logger.warning("无可用会议室")
        return None

    except Exception as e:
        logger.error(f"查找会议室失败: {e}")
        return None


async def create_meeting(
    topic: str,
    duration: int = 30,
    attendees: str | None = None,
    date: str | None = None,
    time: str = "10:00",
    user_open_id: str = "",
    user_access_token: str = "",
) -> dict:
    """创建会议

    Args:
        topic: 会议主题
        duration: 会议时长（分钟）
        attendees: 参会人ID，逗号分隔
        date: 日期 YYYY-MM-DD
        time: 时间 HH:MM
    """
    try:
        attendee_ids = []
        if attendees:
            attendee_ids = [a.strip() for a in attendees.split(",") if a.strip()]

        if user_open_id and user_open_id not in attendee_ids:
            attendee_ids.append(user_open_id)

        attendee_ids = list(dict.fromkeys(attendee_ids))

        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        start_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=duration)

        start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")

        if not feishu_client.is_configured:
            return {"ok": False, "error": "飞书未配置，无法创建会议"}

        if not user_access_token and user_open_id:
            refreshed = await oauth_service.get_user_token_by_open_id_async(user_open_id)
            if refreshed:
                user_access_token = refreshed
                logger.info(f"通过服务端自动刷新获取到用户token: {user_open_id}")

        if not user_access_token:
            logger.warning(f"无有效用户token，将使用应用身份创建会议")

        room_id = await _find_available_room(start_iso, end_iso, user_access_token)
        logger.info(f"预定会议室: {room_id}")

        if user_access_token:
            logger.info(f"使用用户身份创建会议")
            result = await feishu_client.create_calendar_event(
                summary=topic,
                start_time=start_iso,
                end_time=end_iso,
                attendee_ids=attendee_ids if attendee_ids else None,
                user_open_id=user_open_id,
                user_access_token=user_access_token,
                room_id=room_id,
            )
        else:
            logger.info(f"使用应用身份创建会议")
            result = await feishu_client.create_calendar_event(
                summary=topic,
                start_time=start_iso,
                end_time=end_iso,
                attendee_ids=attendee_ids if attendee_ids else None,
                user_open_id=user_open_id,
                room_id=room_id,
            )

        if result.get("error") or result.get("code") != 0:
            error_msg = result.get("error") or result.get("msg") or "飞书API创建日历事件失败"
            logger.error(f"飞书API创建日历事件失败: {result}")
            return {"ok": False, "error": str(error_msg)}

        event_data = result.get("data", {}).get("event", {})
        return {
            "ok": True,
            "message": "会议创建成功，已发送邀请",
            "meeting_id": event_data.get("event_id", event_data.get("id", "")),
            "topic": topic,
            "start_time": start_iso,
            "end_time": end_iso,
            "duration": duration,
            "attendees": attendee_ids,
            "room_id": room_id,
        }

    except Exception as e:
        logger.error(f"创建会议失败: {e}")
        return {"ok": False, "error": str(e)}


def _find_common_free_slot(
    free_busy_info: dict,
    duration_minutes: int,
    date: str,
    start_hour: int = 8,
    end_hour: int = 18,
    exclude_ranges: list[tuple[int, int]] | None = None,
) -> str | None:
    """查找所有用户的公共空挡

    Args:
        free_busy_info: 各用户的忙闲信息
        duration_minutes: 会议时长（分钟）
        date: 日期 YYYY-MM-DD
        start_hour: 开始时间（小时），默认8
        end_hour: 结束时间（小时），默认18
        exclude_ranges: 需排除的时段列表，如 [(12,14)] 表示排除12:00-14:00

    Returns:
        可用的开始时间（HH:MM格式），无空挡返回None
    """
    all_busy_slots = []
    
    for uid, info in free_busy_info.items():
        for slot in info.get("busy_slots", []):
            start_time = slot.get("start_time", "")
            end_time = slot.get("end_time", "")
            if start_time and end_time:
                try:
                    start_dt = datetime.strptime(start_time[:16], "%Y-%m-%dT%H:%M")
                    end_dt = datetime.strptime(end_time[:16], "%Y-%m-%dT%H:%M")
                    all_busy_slots.append((start_dt, end_dt))
                except ValueError:
                    pass

    all_busy_slots.sort(key=lambda x: x[0])

    start_dt = datetime.strptime(f"{date} {start_hour:02d}:00", "%Y-%m-%d %H:%M")
    end_dt = datetime.strptime(f"{date} {end_hour:02d}:00", "%Y-%m-%d %H:%M")
    duration_delta = timedelta(minutes=duration_minutes)

    current_start = start_dt

    while current_start + duration_delta <= end_dt:
        current_end = current_start + duration_delta
        conflict = False

        for busy_start, busy_end in all_busy_slots:
            if current_start < busy_end and current_end > busy_start:
                conflict = True
                current_start = busy_end
                break

        if not conflict and exclude_ranges:
            for ex_start_h, ex_end_h in exclude_ranges:
                ex_start = datetime.strptime(f"{date} {ex_start_h:02d}:00", "%Y-%m-%d %H:%M")
                ex_end = datetime.strptime(f"{date} {ex_end_h:02d}:00", "%Y-%m-%d %H:%M")
                if current_start < ex_end and current_end > ex_start:
                    conflict = True
                    current_start = ex_end
                    break

        if not conflict:
            return current_start.strftime("%H:%M")

        current_start += timedelta(minutes=30)

    return None


async def schedule_meeting(
    user_input: str,
    user_open_id: str = "",
    user_access_token: str = "",
    user_name: str = "",
    confirm: bool = False,
) -> dict:
    """智能安排会议

    使用AI解析用户自然语言输入，查询忙闲状态，找到公共空挡，创建会议。

    Args:
        user_input: 自然语言输入
        user_open_id: 用户open_id
        user_access_token: 用户访问令牌
        confirm: 是否已确认非理想时段
    """
    try:
        if not user_access_token and user_open_id:
            refreshed = await oauth_service.get_user_token_by_open_id_async(user_open_id)
            if refreshed:
                user_access_token = refreshed
                logger.info(f"通过服务端自动刷新获取到用户token: {user_open_id}")

        today = datetime.now().strftime("%Y-%m-%d")
        prompt = MEETING_SCHEDULE_PROMPT.format(user_input=user_input, today=today)
        ai_response = await llm_engine.generate(prompt)
        parsed = parse_ai_json(ai_response)

        if not parsed:
            return {"ok": False, "error": f"AI无法解析会议需求: {user_input}"}

        topic = parsed.get("topic", "")
        duration = parsed.get("duration", 30)
        attendee_names = parsed.get("attendees", [])
        if attendee_names is None:
            attendee_names = []
        date = parsed.get("date", today)
        if date in (None, "null", "None", ""):
            date = today

        logger.info(f"AI解析会议: topic={topic}, duration={duration}, attendees={attendee_names}, date={date}")

        # 搜索参会人 open_id（优先使用用户身份）
        attendee_ids = []
        resolved_attendee_names = []
        my_name = user_name.strip() or "我"
        if attendee_names and feishu_client.is_configured:
            for name in attendee_names:
                # 去掉 @ 前缀（飞书提及语法）
                name = name.lstrip("@")
                if name in ["我", "本人", "自己"] and user_open_id:
                    attendee_ids.append(user_open_id)
                    if not my_name or my_name == "我":
                        try:
                            user_info = await feishu_client.get_user_info(user_open_id, user_access_token=user_access_token)
                            if user_info.get("code") == 0:
                                user_data = user_info.get("data", {}).get("user", {})
                                my_name = user_data.get("name", "我")
                            else:
                                logger.warning(f"获取用户信息失败: {user_info}")
                        except Exception as e:
                            logger.warning(f"获取用户信息失败: {e}")
                    resolved_attendee_names.append(my_name)
                    logger.info(f"找到成员: {name} -> {user_open_id} (姓名: {my_name})")
                else:
                    try:
                        sr = await feishu_client.search_users(name, user_access_token=user_access_token)
                        users = sr.get("data", {}).get("items", [])
                        if users:
                            uid = users[0].get("open_id")
                            display_name = users[0].get("name", name)
                            if uid:
                                attendee_ids.append(uid)
                                resolved_attendee_names.append(display_name)
                                logger.info(f"找到成员: {name} -> {uid}")
                    except Exception as e:
                        logger.warning(f"搜索成员 {name} 失败: {e}")

        # 去重参会人
        attendee_ids = list(dict.fromkeys(attendee_ids))
        resolved_attendee_names = list(dict.fromkeys(resolved_attendee_names))

        # 生成会议标题
        if not topic or topic in ("null", "None", "未命名会议"):
            if resolved_attendee_names:
                topic = "和".join(resolved_attendee_names) + "的会议"
            else:
                topic = "未命名会议"

        # 查询所有参会人的忙闲状态（尝试查找未来7天的空挡）
        if attendee_ids and feishu_client.is_configured:
            logger.info(f"查询参会人忙闲状态: {attendee_ids}")
            
            target_date = datetime.strptime(date, "%Y-%m-%d")
            found_date = None
            found_time = None
            time_quality = "ideal"
            
            for day_offset in range(7):
                search_date = (target_date + timedelta(days=day_offset)).strftime("%Y-%m-%d")
                
                freebusy_result = await query_freebusy(
                    user_ids=",".join(attendee_ids),
                    date=search_date,
                    user_open_id=user_open_id,
                    user_access_token=user_access_token,
                )

                if not freebusy_result.get("ok"):
                    logger.warning(f"忙闲查询失败，使用默认时间: {freebusy_result.get('error')}")
                    continue

                free_busy_info = freebusy_result.get("free_busy", {})
                logger.info(f"忙闲查询结果({search_date}): {free_busy_info}")
                
                # 第一优先：8:00-18:00 且避开12:00-14:00午饭时段
                meeting_time = _find_common_free_slot(
                    free_busy_info=free_busy_info,
                    duration_minutes=duration,
                    date=search_date,
                    start_hour=8,
                    end_hour=18,
                    exclude_ranges=[(12, 14)],
                )
                if meeting_time:
                    found_date = search_date
                    found_time = meeting_time
                    time_quality = "ideal"
                    logger.info(f"找到理想空挡: {search_date} {meeting_time}")
                    break

                # 第二优先：12:00-14:00 午饭时段
                meeting_time = _find_common_free_slot(
                    free_busy_info=free_busy_info,
                    duration_minutes=duration,
                    date=search_date,
                    start_hour=12,
                    end_hour=14,
                )
                if meeting_time:
                    found_date = search_date
                    found_time = meeting_time
                    time_quality = "lunch"
                    logger.info(f"午饭时段找到空挡: {search_date} {meeting_time}")
                    break

                # 第三优先：18:00-20:00 加班时段
                meeting_time = _find_common_free_slot(
                    free_busy_info=free_busy_info,
                    duration_minutes=duration,
                    date=search_date,
                    start_hour=18,
                    end_hour=20,
                )
                if meeting_time:
                    found_date = search_date
                    found_time = meeting_time
                    time_quality = "overtime"
                    logger.info(f"加班时段找到空挡: {search_date} {meeting_time}")
                    break

            if not found_date:
                return {
                    "ok": False,
                    "error": f"在未来7天内，未找到所有参会人共同的 {duration} 分钟空挡",
                }
            
            # 检查是否周末
            found_dt = datetime.strptime(found_date, "%Y-%m-%d")
            if found_dt.weekday() >= 5:
                time_quality = "weekend"
                logger.info(f"安排日期为周末: {found_date}")
            
            # 未确认时统一返回确认请求（ideal/非ideal 都需用户确认）
            if not confirm:
                start_dt = datetime.strptime(f"{found_date} {found_time}", "%Y-%m-%d %H:%M")
                end_dt = start_dt + timedelta(minutes=duration)
                return {
                    "ok": True,
                    "needs_confirmation": True,
                    "time_quality": time_quality,
                    "topic": topic,
                    "date": found_date,
                    "start_time": found_time,
                    "end_time": end_dt.strftime("%H:%M"),
                    "duration": duration,
                    "attendees": attendee_ids,
                    "resolved_attendee_names": resolved_attendee_names,
                }
            
            date = found_date
            meeting_time = found_time
            logger.info(f"使用空挡: {date} {meeting_time} (质量: {time_quality})")
        else:
            meeting_time = "10:00"
            time_quality = "ideal"
            logger.info("飞书未配置或无参会人，使用默认时间")

        # 创建会议
        logger.info(f"准备创建会议: topic={topic}, date={date}, time={meeting_time}, duration={duration}, attendees={attendee_ids}")
        create_result = await create_meeting(
            topic=topic,
            duration=duration,
            attendees=",".join(attendee_ids) if attendee_ids else None,
            date=date,
            time=meeting_time,
            user_open_id=user_open_id,
            user_access_token=user_access_token,
        )
        logger.info(f"会议创建结果: {create_result}")
        return create_result

    except Exception as e:
        logger.error(f"智能安排会议失败: {e}")
        return {"ok": False, "error": str(e)}


async def query_freebusy(
    user_ids: str,
    date: str | None = None,
    user_open_id: str = "",
    user_access_token: str = "",
) -> dict:
    """查询忙闲状态

    Args:
        user_ids: 用户ID，逗号分隔
        date: 日期 YYYY-MM-DD
        user_open_id: 用户open_id
        user_access_token: 用户访问令牌
    """
    try:
        if not user_access_token and user_open_id:
            refreshed = await oauth_service.get_user_token_by_open_id_async(user_open_id)
            if refreshed:
                user_access_token = refreshed
                logger.info(f"通过服务端自动刷新获取到用户token: {user_open_id}")

        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        ids = [uid.strip() for uid in user_ids.split(",") if uid.strip()]

        start_time = f"{date}T08:00:00+08:00"
        end_time = f"{date}T20:00:00+08:00"

        if not feishu_client.is_configured:
            return {"ok": False, "error": "飞书未配置，无法查询忙闲状态"}

        result = await feishu_client.query_free_busy(
            user_ids=ids,
            start_time=start_time,
            end_time=end_time,
            user_access_token=user_access_token,
        )
        if result.get("error"):
            return {
                "ok": False,
                "error": result.get("msg", "查询忙闲失败"),
            }

        users_data = result.get("data", {}).get("users", {})

        free_busy_info = {}
        for uid in ids:
            user_info = users_data.get(uid, {})
            free_busy_info[uid] = {
                "busy_slots": user_info.get("busy_slots", []),
                "busy_count": user_info.get("busy_count", 0),
            }

        return {
            "ok": True,
            "message": f"查询了 {len(ids)} 个用户的忙闲状态",
            "date": date,
            "free_busy": free_busy_info,
        }

    except Exception as e:
        logger.error(f"查询忙闲状态失败: {e}")
        return {"ok": False, "error": str(e)}


async def search_member(
    keyword: str,
    user_open_id: str = "",
    user_access_token: str = "",
) -> dict:
    """搜索组织成员

    Args:
        keyword: 搜索关键词
        user_open_id: 用户open_id
        user_access_token: 用户访问令牌
    """
    try:
        if not user_access_token and user_open_id:
            refreshed = await oauth_service.get_user_token_by_open_id_async(user_open_id)
            if refreshed:
                user_access_token = refreshed
                logger.info(f"通过服务端自动刷新获取到用户token: {user_open_id}")

        if not feishu_client.is_configured:
            return {"ok": False, "error": "飞书未配置，无法搜索成员"}

        result = await feishu_client.search_users(keyword, user_access_token=user_access_token)
        users = result.get("data", {}).get("items", [])
        
        members = []
        for u in users:
            name = u.get("name", "")
            if not name:
                name = u.get("user_name", "")
                if not name:
                    name = u.get("display_name", "")
            
            open_id = u.get("open_id", "")
            if not open_id:
                open_id = u.get("user_id", "")
            
            members.append({
                "name": name,
                "open_id": open_id,
                "department": u.get("department_ids", []),
            })
        
        return {
            "ok": True,
            "message": f"找到 {len(members)} 个成员",
            "members": members,
        }
    except Exception as e:
        logger.error(f"搜索成员失败: {e}")
        return {"ok": False, "error": str(e)}


async def get_meeting_detail(
    calendar_id: str,
    event_id: str,
    user_open_id: str = "",
    user_access_token: str = "",
) -> dict:
    """查询会议详情

    Args:
        calendar_id: 日历ID
        event_id: 会议ID
        user_open_id: 用户open_id
        user_access_token: 用户访问令牌
    """
    try:
        if not user_access_token and user_open_id:
            refreshed = await oauth_service.get_user_token_by_open_id_async(user_open_id)
            if refreshed:
                user_access_token = refreshed
                logger.info(f"通过服务端自动刷新获取到用户token: {user_open_id}")

        if feishu_client.is_configured:
            result = await feishu_client.get_event_detail(
                calendar_id=calendar_id,
                event_id=event_id,
                user_access_token=user_access_token,
            )
            if result.get("error") or result.get("code") != 0:
                logger.warning(f"查询会议详情失败: {result}")
                return {"ok": False, "error": result.get("msg", "查询失败")}

            event_data = result.get("data", {}).get("event", {})
            attendees = event_data.get("attendees", [])
            
            if not attendees:
                logger.info("会议详情未返回参会人，使用独立接口查询")
                attendees_result = await feishu_client.get_event_attendees(
                    calendar_id=calendar_id,
                    event_id=event_id,
                    user_access_token=user_access_token,
                )
                if attendees_result.get("code") == 0:
                    attendees = attendees_result.get("data", {}).get("items", [])
                    logger.info(f"独立接口查询到参会人: {len(attendees)} 人")
            
            attendee_list = []
            for a in attendees:
                attendee_list.append({
                    "name": a.get("display_name", ""),
                    "user_id": a.get("user_id", ""),
                    "user_id_type": a.get("user_id_type", ""),
                    "status": a.get("status", ""),
                    "type": a.get("type", ""),
                })

            return {
                "ok": True,
                "message": "查询成功",
                "event": event_data,
                "attendees": attendee_list,
            }
        else:
            return {
                "ok": False,
                "error": "飞书未配置",
            }
    except Exception as e:
        logger.error(f"查询会议详情失败: {e}")
        return {"ok": False, "error": str(e)}
