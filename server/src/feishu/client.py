"""飞书API客户端

封装飞书开放平台API调用，包括：
- 获取 tenant_access_token
- 发送消息
- 基础API调用
"""

import time
import httpx
import logging
import json
from datetime import datetime

from server.src.core.config import server_config

logger = logging.getLogger("lanshan-server.feishu")


class FeishuClient:
    """飞书API客户端"""

    BASE_URL = "https://open.feishu.cn/open-apis"
    TENANT_DOMAIN = "ecnp67jgx129.feishu.cn"  # 租户域名，用于生成多维表格UI链接

    def __init__(self):
        self.app_id = server_config.feishu_app_id
        self.app_secret = server_config.feishu_app_secret
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    @property
    def is_configured(self) -> bool:
        return bool(self.app_id and self.app_secret)

    async def _get_access_token(self, force_refresh: bool = False) -> str:
        """获取或刷新 tenant_access_token"""
        if not force_refresh and self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        url = f"{self.BASE_URL}/auth/v3/tenant_access_token/internal"
        body = {
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"获取飞书access_token失败: {data}")

        self._access_token = data["tenant_access_token"]
        self._token_expires_at = time.time() + data.get("expire", 7200)
        logger.info(f"飞书 access_token 已刷新: {self._access_token[:20]}...")
        return self._access_token

    async def _request(self, method: str, path: str, user_access_token: str = "", use_app_token: bool = False, **kwargs) -> dict:
        """带认证的HTTP请求

        发生HTTP错误时不抛出异常，而是返回包含错误信息的dict。
        遇到401时自动刷新token并重试一次。

        核心原则：
        - 优先使用用户身份token
        - 用户token过期时优先尝试刷新用户token
        - 用户token刷新失败时，不回退到应用身份，返回错误提示用户重新授权
        - use_app_token=True时强制使用应用身份token

        Args:
            method: HTTP方法
            path: API路径
            user_access_token: 用户访问令牌（可选，优先使用）
            use_app_token: 是否强制使用应用身份token
            **kwargs: 其他请求参数
        """
        try:
            if use_app_token:
                token = await self._get_access_token()
                logger.debug(f"使用应用身份token请求: {method} {path}")
            elif user_access_token:
                token = user_access_token
                logger.debug(f"使用用户身份token请求: {method} {path}")
            else:
                token = await self._get_access_token()
                logger.debug(f"使用应用身份token请求: {method} {path}")

            original_headers = kwargs.pop("headers", {})
            headers = dict(original_headers)
            headers["Authorization"] = f"Bearer {token}"
            url = f"{self.BASE_URL}{path}"

            logger.debug(f"请求URL: {url}")
            logger.debug(f"请求Headers: {dict(headers)}")
            logger.info(f"请求Body: {kwargs.get('json', 'N/A')}")

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.request(method, url, headers=headers, **kwargs)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            logger.warning(f"飞书API请求失败: {method} {path} -> {e.response.status_code}: {e.response.text[:300]}")

            # 提取飞书响应体中的错误码
            feishu_code = e.response.status_code
            feishu_msg = e.response.text[:500]
            try:
                resp_body = json.loads(e.response.text)
                if "code" in resp_body and isinstance(resp_body["code"], int) and resp_body["code"] != 0:
                    feishu_code = resp_body["code"]
                    feishu_msg = resp_body.get("msg", feishu_msg)
            except (json.JSONDecodeError, TypeError):
                pass

            # 飞书 token 相关错误码（可能伴随 HTTP 400 或 401）
            # 99991677: token 过期 / 99991668: token 无效
            FEISHU_TOKEN_ERROR_CODES = {99991677, 99991668}

            if e.response.status_code == 401 or feishu_code in FEISHU_TOKEN_ERROR_CODES:
                if user_access_token:
                    logger.info("用户token过期/无效，尝试刷新用户token并重试...")
                    try:
                        from server.src.services.auth_service import oauth_service
                        token_info = oauth_service.get_user_token_info_by_access_token(user_access_token)
                        if not token_info.get("ok"):
                            logger.info("通过access_token未找到用户，尝试获取第一个存储的用户")
                            token_info = oauth_service.get_stored_user_info()

                        if token_info.get("ok"):
                            refresh_token = token_info.get("refresh_token", "")
                            if refresh_token:
                                refresh_result = await oauth_service.refresh_user_token(refresh_token)
                                if refresh_result.get("ok"):
                                    new_token = refresh_result.get("access_token", "")
                                    if new_token:
                                        retry_headers = dict(original_headers)
                                        retry_headers["Authorization"] = f"Bearer {new_token}"
                                        async with httpx.AsyncClient(timeout=30) as client:
                                            resp = await client.request(method, url, headers=retry_headers, **kwargs)
                                            resp.raise_for_status()
                                            logger.info(f"用户token刷新后重试成功: {method} {path}")
                                            return resp.json()
                                else:
                                    logger.warning(f"用户token刷新失败: {refresh_result.get('msg', '未知错误')}")
                            else:
                                logger.warning("没有refresh_token，无法自动刷新用户token")
                        else:
                            logger.warning("服务端没有存储用户token")
                    except Exception as refresh_e:
                        logger.warning(f"刷新用户token失败: {refresh_e}")

                    return {
                        "code": 401,
                        "msg": "用户身份token过期，请重新授权",
                        "error": True,
                        "need_reauth": True,
                    }

                logger.info("应用身份token过期，尝试刷新并重试...")
                try:
                    token = await self._get_access_token(force_refresh=True)
                    retry_headers = dict(original_headers)
                    retry_headers["Authorization"] = f"Bearer {token}"
                    async with httpx.AsyncClient(timeout=30) as client:
                        resp = await client.request(method, url, headers=retry_headers, **kwargs)
                        resp.raise_for_status()
                        logger.info(f"应用身份token刷新后重试成功: {method} {path}")
                        return resp.json()
                except Exception as retry_e:
                    logger.warning(f"刷新应用身份token后重试仍然失败: {retry_e}")

            return {"code": feishu_code, "msg": feishu_msg, "error": True, "http_status": e.response.status_code}
        except Exception as e:
            logger.warning(f"飞书API请求异常: {method} {path} -> {e}")
            return {"code": -1, "msg": str(e), "error": True}

    async def send_message(self, receive_id: str, content: str, msg_type: str = "text", receive_id_type: str = "open_id", user_access_token: str = "", use_app_token: bool = False) -> dict:
        """发送飞书消息

        Args:
            receive_id: 接收者ID（用户open_id或群聊chat_id）
            content: 消息内容
            msg_type: 消息类型（text/interactive）
            receive_id_type: ID类型（open_id/chat_id）
            user_access_token: 用户访问令牌（可选，优先使用）
            use_app_token: 是否使用应用身份token发送
        """
        body = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": content,
        }
        return await self._request(
            "POST",
            f"/im/v1/messages?receive_id_type={receive_id_type}",
            json=body,
            user_access_token=user_access_token if not use_app_token else "",
            use_app_token=use_app_token,
        )

    async def upload_image(self, image_bytes: bytes, image_type: str = "png") -> dict:
        """上传图片到飞书，返回 image_key
        
        Args:
            image_bytes: 图片二进制数据
            image_type: 图片类型（png/jpeg/gif/webp/bmp）
        """
        import io
        from httpx import AsyncClient

        token = await self._get_access_token()
        url = f"{self.BASE_URL}/im/v1/images"

        try:
            files = {
                "image": (
                    f"image.{image_type}",
                    io.BytesIO(image_bytes),
                    f"image/{image_type}",
                )
            }
            headers = {"Authorization": f"Bearer {token}"}
            async with AsyncClient(timeout=30) as client:
                resp = await client.post(url, headers=headers, files=files)
                resp.raise_for_status()
                result = resp.json()
                logger.info(f"图片上传成功: image_key={result.get('data', {}).get('image_key', '')}")
                return result
        except Exception as e:
            logger.warning(f"图片上传失败: {e}")
            return {"error": True, "code": -1, "msg": str(e)}

    async def search_chats(self, keyword: str, user_access_token: str = "") -> dict:
        """搜索群聊列表

        Args:
            keyword: 群名关键词
        """
        return await self._request(
            "GET",
            f"/im/v1/chats?page_size=20&keyword={keyword}",
            user_access_token=user_access_token,
        )

    async def get_user_info(self, open_id: str, user_access_token: str = "") -> dict:
        """获取用户信息"""
        return await self._request(
            "GET",
            f"/contact/v3/users/{open_id}",
            params={"user_id_type": "open_id"},
            user_access_token=user_access_token,
        )

    async def get_user_full_info(self, open_id: str, user_access_token: str = "") -> dict:
        """获取用户完整通讯录信息（包含部门、职级、员工类型等）

        返回数据结构：
        {
            "code": 0,
            "data": {
                "user": {
                    "open_id": "ou_xxx",
                    "name": "张三",
                    "department_ids": ["od_xxx", "od_yyy"],
                    "job_level_id": "level_xxx",
                    "employee_type": "正式员工",
                    "department": "技术部"
                }
            }
        }
        """
        result = await self._request(
            "GET",
            f"/contact/v3/users/{open_id}",
            params={"user_id_type": "open_id"},
            user_access_token=user_access_token,
        )

        if result.get("code") != 0:
            return result

        user_data = result.get("data", {}).get("user", {})
        return {
            "code": 0,
            "data": {
                "user": {
                    "open_id": user_data.get("open_id", ""),
                    "name": user_data.get("name", ""),
                    "department_ids": user_data.get("department_ids", []),
                    "job_level_id": user_data.get("job_level_id", ""),
                    "employee_type": user_data.get("employee_type", ""),
                    "department": user_data.get("department", ""),
                }
            }
        }

    async def health_check(self) -> dict:
        """验证飞书连接是否正常

        尝试获取access_token，成功即表示凭证配置正确。
        """
        try:
            token = await self._get_access_token()
            return {
                "ok": True,
                "message": "飞书API连接正常",
                "token_prefix": token[:8] + "..." if token else "无",
            }
        except Exception as e:
            return {
                "ok": False,
                "message": f"飞书API连接失败: {str(e)}",
            }


    # ======================== 任务 API ========================

    async def create_task(
        self,
        summary: str,
        due_date: str | None = None,
        due_time: str | None = None,
        description: str | None = None,
        user_access_token: str = "",
        assignee_ids: list[str] | None = None,
    ) -> dict:
        """创建飞书任务

        Args:
            summary: 任务标题
            due_date: 截止日期 YYYY-MM-DD
            due_time: 截止时间 HH:MM（可选）
            description: 任务描述
            assignee_ids: 负责人 open_id 列表
        """
        body = {
            "summary": summary,
            "origin": {
                "platform_i18n_name": "{\"zh_cn\": \"AI Agent飞书助手\", \"en_us\": \"AI Agent Feishu Assistant\"}",
            },
        }
        if assignee_ids:
            body["assignee_ids"] = assignee_ids
        if due_date:
            if due_time:
                due_dt = datetime.strptime(f"{due_date} {due_time}", "%Y-%m-%d %H:%M")
                is_all_day = False
            else:
                due_dt = datetime.strptime(due_date, "%Y-%m-%d")
                is_all_day = True
            due_timestamp = str(int(due_dt.timestamp()))
            body["due"] = {
                "time": due_timestamp,
                "timezone": "Asia/Shanghai",
                "is_all_day": is_all_day,
            }
        if description:
            body["description"] = description

        return await self._request(
            "POST",
            "/task/v1/tasks?user_id_type=open_id",
            json=body,
            user_access_token=user_access_token,
        )

    async def add_task_collaborator(self, task_id: str, user_id: str, user_access_token: str = "") -> dict:
        """添加任务执行者
        
        Args:
            task_id: 任务ID
            user_id: 用户open_id
            user_access_token: 用户访问令牌
        """
        body = {"id_list": [user_id]}
        return await self._request(
            "POST",
            f"/task/v1/tasks/{task_id}/collaborators?user_id_type=open_id",
            json=body,
            user_access_token=user_access_token,
        )

    async def list_tasks(self, start_time: str | None = None, end_time: str | None = None, user_access_token: str = "", assignee_ids: list = None) -> dict:
        """获取任务列表"""
        params = {"page_size": 100, "user_id_type": "open_id"}
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        if assignee_ids:
            params["assignee_ids"] = ",".join(assignee_ids)

        return await self._request(
            "GET",
            "/task/v1/tasks",
            params=params,
            user_access_token=user_access_token,
        )

    async def list_related_tasks(self, completed: bool | None = None, page_size: int = 50, page_token: str = "", user_access_token: str = "") -> dict:
        """列取与我相关的任务
        获取任务中心我负责的、我关注的、我创建的、我分配的任务列表
        
        Args:
            completed: 是否按任务完成进行过滤。不填写表示不过滤。true返回已完成任务，false返回未完成任务
            page_size: 分页大小，默认50，最大100
            page_token: 分页标记
            user_access_token: 用户访问令牌（必须使用用户身份）
        
        Returns:
            任务列表，包含任务guid、summary、due、members等信息
        """
        params = {"page_size": page_size, "user_id_type": "open_id"}
        if completed is not None:
            params["completed"] = str(completed).lower()
        if page_token:
            params["page_token"] = page_token

        return await self._request(
            "GET",
            "/task/v2/task_v2/list_related_task",
            params=params,
            user_access_token=user_access_token,
        )

    async def update_task(self, task_guid: str, **kwargs) -> dict:
        """更新任务信息
        
        Args:
            task_guid: 任务guid
            **kwargs: 更新字段（summary, due, status等）
        
        Returns:
            API响应
        """
        body = {}
        update_fields = []
        if "summary" in kwargs:
            body["summary"] = kwargs["summary"]
            update_fields.append("summary")
        if "status" in kwargs:
            body["status"] = kwargs["status"]
            update_fields.append("status")
        if "due" in kwargs:
            body["due"] = kwargs["due"]
            update_fields.append("due")
        if "description" in kwargs:
            body["description"] = kwargs["description"]
            update_fields.append("description")

        return await self._request(
            "PATCH",
            f"/task/v1/tasks/{task_guid}",
            json={"task": body, "update_fields": update_fields},
            user_access_token=kwargs.get("user_access_token", ""),
        )

    async def complete_task(self, task_guid: str, user_access_token: str = "") -> dict:
        """完成任务
        
        Args:
            task_guid: 任务guid
            user_access_token: 用户访问令牌
        
        Returns:
            API响应
        """
        return await self._request(
            "POST",
            f"/task/v1/tasks/{task_guid}/complete",
            json={},
            user_access_token=user_access_token,
        )

    # ======================== 日历 API ========================

    async def query_free_busy(
        self,
        user_ids: list[str],
        start_time: str,
        end_time: str,
        user_access_token: str = "",
    ) -> dict:
        """查询用户忙闲状态

        Args:
            user_ids: 用户open_id列表
            start_time: 开始时间 ISO格式
            end_time: 结束时间 ISO格式

        Returns:
            按用户ID分组的忙闲状态字典，格式:
            {"code": 0, "msg": "success", "data": {"users": {"user_id": {"busy_slots": [...]}}}}
        """
        users_result = {}
        for uid in user_ids:
            body = {
                "time_min": start_time,
                "time_max": end_time,
                "user_id": uid,
            }
            result = await self._request(
                "POST",
                "/calendar/v4/freebusy/list?user_id_type=open_id",
                json=body,
                user_access_token=user_access_token,
            )
            if result.get("error") or result.get("code") != 0:
                logger.info(f"用户身份查询忙闲失败，尝试应用身份: {uid}")
                result = await self._request(
                    "POST",
                    "/calendar/v4/freebusy/list?user_id_type=open_id",
                    json=body,
                )
                if result.get("error") or result.get("code") != 0:
                    logger.warning(f"应用身份查询忙闲也失败: {uid}")
                    return result
            
            freebusy_list = result.get("data", {}).get("freebusy_list", [])
            users_result[uid] = {
                "busy_slots": freebusy_list,
                "busy_count": len(freebusy_list),
            }
        
        return {
            "code": 0,
            "msg": "success",
            "data": {
                "users": users_result,
            },
        }

    async def create_shared_calendar(self, name: str, user_open_id: str = "") -> str | None:
        """创建共享日历

        Args:
            name: 日历名称
            user_open_id: 创建者open_id

        Returns:
            共享日历ID，失败返回None
        """
        body = {
            "summary": name,
            "description": "AI智能安排会议共享日历",
        }

        result = await self._request(
            "POST",
            "/calendar/v4/calendars",
            json=body,
        )
        if result.get("error") or result.get("code") != 0:
            logger.warning(f"创建共享日历失败: {result}")
            return None

        calendar_id = result.get("data", {}).get("calendar", {}).get("calendar_id")
        logger.info(f"创建共享日历成功: {calendar_id}")
        return calendar_id

    async def add_calendar_permission(
        self,
        calendar_id: str,
        user_id: str,
        role: str = "reader",
        user_id_type: str = "open_id",
    ) -> bool:
        """添加日历权限

        Args:
            calendar_id: 日历ID
            user_id: 用户ID
            role: 权限角色 (reader/writer/owner)
            user_id_type: 用户ID类型

        Returns:
            是否成功
        """
        body = {
            "permissions": [{
                "role": role,
                "user_id": user_id,
                "user_id_type": user_id_type,
            }]
        }
        result = await self._request(
            "PATCH",
            f"/calendar/v4/calendars/{calendar_id}/permissions",
            json=body,
        )
        return not (result.get("error") or result.get("code") != 0)

    async def add_event_attendees(
        self,
        calendar_id: str,
        event_id: str,
        attendee_ids: list[str],
        room_id: str = "",
        user_access_token: str = "",
    ) -> dict:
        """添加日程参会人（使用官方添加参会人接口）

        Args:
            calendar_id: 日历ID
            event_id: 日程ID
            attendee_ids: 用户open_id列表
            room_id: 会议室ID
            user_access_token: 用户访问令牌

        Returns:
            API响应
        """
        attendees = []
        for uid in attendee_ids:
            attendees.append({
                "type": "user",
                "user_id": uid,
            })
        if room_id:
            attendees.append({
                "type": "resource",
                "room_id": room_id,
            })

        body = {
            "attendees": attendees,
            "need_notification": True,
        }

        result = await self._request(
            "POST",
            f"/calendar/v4/calendars/{calendar_id}/events/{event_id}/attendees?user_id_type=open_id",
            json=body,
            user_access_token=user_access_token,
        )
        logger.info(f"添加参会人响应: {json.dumps(result, ensure_ascii=False)[:500]}")
        return result

    async def create_calendar_event(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        attendee_ids: list[str] | None = None,
        description: str | None = None,
        user_open_id: str = "",
        user_access_token: str = "",
        room_id: str = "",
    ) -> dict:
        """创建日历事件（会议）

        按照飞书官方推荐流程：
        1. 创建共享日历（使用应用身份）
        2. 在共享日历中创建日程（使用应用身份）
        3. 添加参会人（使用应用身份，触发邀请通知）

        Args:
            summary: 会议主题
            start_time: 开始时间 ISO格式
            end_time: 结束时间 ISO格式
            attendee_ids: 参会人 open_id 列表
            description: 会议描述
            user_open_id: 当前用户open_id（作为组织者）
            room_id: 会议室ID（可选）
        """
        def _to_timestamp(iso_str: str) -> str:
            try:
                dt = datetime.fromisoformat(iso_str)
                return str(int(dt.timestamp()))
            except Exception:
                return iso_str

        attendees = []
        if attendee_ids:
            for uid in attendee_ids:
                attendees.append({
                    "type": "user",
                    "user_id": uid,
                    "user_id_type": "open_id",
                })
        if room_id:
            attendees.append({
                "type": "resource",
                "resource_id": room_id,
            })

        if user_open_id and user_open_id not in (a.get("user_id") for a in attendees if a.get("type") == "user"):
            attendees.append({
                "type": "user",
                "user_id": user_open_id,
                "user_id_type": "open_id",
            })

        body = {
            "summary": summary,
            "start_time": {"timestamp": _to_timestamp(start_time), "timezone": "Asia/Shanghai"},
            "end_time": {"timestamp": _to_timestamp(end_time), "timezone": "Asia/Shanghai"},
            "attendees": attendees,
            "notify_participants": True,
        }

        if user_open_id:
            body["organizer"] = {
                "user_id": user_open_id,
                "user_id_type": "open_id",
            }

        if description:
            body["description"] = description

        logger.info(f"创建会议请求体: {json.dumps(body, ensure_ascii=False)}")

        if user_access_token:
            logger.info("使用用户身份创建会议")
            calendars_result = await self._request(
                "GET",
                "/calendar/v4/calendars?user_id_type=open_id",
                user_access_token=user_access_token,
            )
            calendar_list = calendars_result.get("data", {}).get("calendar_list", [])
            primary_calendar_id = ""
            for cal in calendar_list:
                if cal.get("is_primary"):
                    primary_calendar_id = cal.get("calendar_id", "")
                    break
            if not primary_calendar_id and calendar_list:
                primary_calendar_id = calendar_list[0].get("calendar_id", "")
            
            if primary_calendar_id:
                logger.info(f"使用主日历创建会议: {primary_calendar_id}")
                create_result = await self._request(
                    "POST",
                    f"/calendar/v4/calendars/{primary_calendar_id}/events?user_id_type=open_id",
                    json=body,
                    user_access_token=user_access_token,
                )
                if create_result.get("code") == 0 and attendee_ids and len(attendee_ids) > 1:
                    event_data = create_result.get("data", {}).get("event", {})
                    event_id = event_data.get("event_id", "")
                    if event_id:
                        add_result = await self.add_event_attendees(
                            calendar_id=primary_calendar_id,
                            event_id=event_id,
                            attendee_ids=attendee_ids,
                            room_id=room_id,
                            user_access_token=user_access_token,
                        )
                        if add_result.get("code") == 0:
                            logger.info(f"添加参会人成功，触发邀请通知")
                        else:
                            logger.warning(f"添加参会人失败: {add_result}")
                result = create_result
            else:
                logger.warning("未找到用户主日历，使用默认路径")
                result = await self._request(
                    "POST",
                    "/calendar/v4/calendars/primary/events?user_id_type=open_id",
                    json=body,
                    user_access_token=user_access_token,
                )
        else:
            logger.info("使用应用身份创建会议（共享日历流程）")
            shared_calendar_id = await self.create_shared_calendar("AI会议共享日历")
            if shared_calendar_id:
                body_no_attendees = body.copy()
                body_no_attendees["attendees"] = []
                body_no_attendees["notify_participants"] = False
                create_result = await self._request(
                    "POST",
                    f"/calendar/v4/calendars/{shared_calendar_id}/events?user_id_type=open_id",
                    json=body_no_attendees,
                )
                if create_result.get("code") == 0:
                    event_data = create_result.get("data", {}).get("event", {})
                    event_id = event_data.get("event_id", "")
                    if event_id and attendee_ids:
                        add_result = await self.add_event_attendees(
                            calendar_id=shared_calendar_id,
                            event_id=event_id,
                            attendee_ids=attendee_ids,
                            room_id=room_id,
                        )
                        if add_result.get("code") == 0:
                            logger.info(f"添加参会人成功，触发邀请通知")
                        else:
                            logger.warning(f"添加参会人失败: {add_result}")
                result = create_result
            else:
                result = {"error": True, "code": -1, "msg": "共享日历创建失败"}

        logger.info(f"创建会议响应: {json.dumps(result, ensure_ascii=False)[:500]}")
        return result

    async def list_meeting_rooms(self, user_access_token: str = "") -> dict:
        """查询会议室列表"""
        return await self._request(
            "GET",
            "/calendar/v4/resources/rooms",
            user_access_token=user_access_token,
        )

    async def query_meeting_room_freebusy(
        self,
        room_ids: list[str],
        time_min: str,
        time_max: str,
        user_access_token: str = "",
    ) -> dict:
        """查询会议室忙闲状态"""
        body = {
            "room_ids": room_ids,
            "time_min": time_min,
            "time_max": time_max,
        }
        return await self._request(
            "POST",
            "/calendar/v4/resources/rooms/freebusy",
            json=body,
            user_access_token=user_access_token,
        )

    async def get_event_detail(self, calendar_id: str, event_id: str, user_access_token: str = "") -> dict:
        """查询会议详情"""
        result = await self._request(
            "GET",
            f"/calendar/v4/calendars/{calendar_id}/events/{event_id}?user_id_type=open_id",
            user_access_token=user_access_token,
        )
        return result

    async def get_event_attendees(self, calendar_id: str, event_id: str, user_access_token: str = "") -> dict:
        """查询会议参会人列表"""
        result = await self._request(
            "GET",
            f"/calendar/v4/calendars/{calendar_id}/events/{event_id}/attendees?user_id_type=open_id",
            user_access_token=user_access_token,
        )
        return result

    # ======================== 文档 API ========================

    async def fetch_document(self, doc_token: str, user_access_token: str = "") -> dict:
        """获取飞书文档内容

        Args:
            doc_token: 文档token（从文档URL中提取）
            user_access_token: 用户访问令牌
        """
        return await self._request(
            "GET",
            f"/docx/v1/documents/{doc_token}/raw_content",
            user_access_token=user_access_token,
        )

    async def fetch_file_content(self, file_token: str, user_access_token: str = "") -> dict:
        """获取飞书文件内容（支持 /file/ 链接，如 .docx）

        Args:
            file_token: 文件token（从 /file/ URL中提取）
            user_access_token: 用户访问令牌
        """
        import io
        try:
            import docx
        except ImportError:
            return {"code": -1, "msg": "python-docx not installed", "data": {"content": "", "title": ""}}

        token = user_access_token if user_access_token else await self._get_access_token()
        url = f"{self.BASE_URL}/drive/v1/files/{file_token}/download"

        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code != 200:
                return {"code": resp.status_code, "msg": f"下载文件失败: HTTP {resp.status_code}",
                        "data": {"content": "", "title": ""}}

            content_type = resp.headers.get("content-type", "")
            content = resp.content

            # 提取文件名
            filename = ""
            disposition = resp.headers.get("content-disposition", "")
            if disposition:
                import re
                fn_match = re.search(r'filename[^;=\n]*=((["\']).*?\2|[^;\n]*)', disposition)
                if fn_match:
                    filename = fn_match.group(1).strip("\"'")

            # 根据文件类型提取文本
            text = ""
            if "wordprocessingml" in content_type or "docx" in content_type:
                try:
                    doc = docx.Document(io.BytesIO(content))
                    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
                    text = "\n".join(paragraphs)
                    logger.info(f"从docx文件提取文本，共 {len(paragraphs)} 段")
                except Exception as e:
                    logger.warning(f"解析docx文件失败: {e}")
                    return {"code": -1, "msg": f"解析docx失败: {e}", "data": {"content": "", "title": filename}}
            elif "text" in content_type:
                text = content.decode("utf-8", errors="replace")
            else:
                return {"code": -1, "msg": f"不支持的文件类型: {content_type}",
                        "data": {"content": "", "title": filename}}

            return {"code": 0, "msg": "success",
                    "data": {"content": text, "title": filename or file_token}}

    # ======================== 通讯录 API ========================

    async def search_users(self, query: str, user_access_token: str = "") -> dict:
        """搜索组织成员"""
        return await self._request(
            "GET",
            f"/contact/v3/users?page_size=20&name={query}",
            user_access_token=user_access_token,
        )

    # ======================== 多维表格 API ========================

    async def bitable_list_tables(self, app_token: str, user_access_token: str = "") -> dict:
        """获取多维表格数据表列表

        Args:
            app_token: 多维表格应用token
        """
        return await self._request(
            "GET",
            f"/bitable/v1/apps/{app_token}/tables",
            user_access_token=user_access_token,
        )

    async def bitable_read_records(
        self,
        app_token: str,
        table_id: str,
        fields: list[str] | None = None,
        filter: str | None = None,
        page_size: int = 100,
        page_token: str | None = None,
        user_access_token: str = "",
    ) -> dict:
        """读取多维表格记录

        Args:
            app_token: 多维表格应用token
            table_id: 表格ID
            fields: 需要返回的字段列表（可选）
            filter: 筛选条件（可选，JSON格式）
            page_size: 每页数量
            page_token: 分页标记（可选，用于获取下一页）
        """
        params = {"page_size": page_size}
        if fields:
            params["field_names"] = ",".join(fields)
        if filter:
            params["filter"] = filter
        if page_token:
            params["page_token"] = page_token

        return await self._request(
            "GET",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            params=params,
            user_access_token=user_access_token,
        )

    async def bitable_get_schema(self, app_token: str, table_id: str, user_access_token: str = "") -> dict:
        """获取多维表格结构元数据

        Args:
            app_token: 多维表格应用token
            table_id: 表格ID
        """
        return await self._request(
            "GET",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            user_access_token=user_access_token,
        )

    async def bitable_create_app(self, name: str, user_access_token: str = "") -> dict:
        """创建多维表格应用

        Args:
            name: 应用名称
        """
        return await self._request(
            "POST",
            "/bitable/v1/apps",
            json={"name": name},
            user_access_token=user_access_token,
        )

    async def bitable_add_records(
        self,
        app_token: str,
        table_id: str,
        records: list[dict],
        user_access_token: str = "",
    ) -> dict:
        """添加多维表格记录

        Args:
            app_token: 多维表格应用token
            table_id: 表格ID
            records: 记录数据列表
        """
        return await self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
            json={"records": records},
            user_access_token=user_access_token,
        )

    async def bitable_create_table(self, app_token: str, name: str, user_access_token: str = "") -> dict:
        """创建多维表格

        Args:
            app_token: 多维表格应用token
            name: 表格名称
        """
        return await self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables",
            json={"table": {"name": name, "default_view_name": "默认视图", "fields": [{"field_name": "索引", "type": 1}]}},
            user_access_token=user_access_token,
        )

    async def bitable_create_field(
        self,
        app_token: str,
        table_id: str,
        field_name: str,
        field_type: str,
        options: list = None,
        user_access_token: str = "",
    ) -> dict:
        """创建多维表格字段

        Args:
            app_token: 多维表格应用token
            table_id: 表格ID
            field_name: 字段名称
            field_type: 字段类型（text/select/datetime/user等）
            options: 选项列表（select类型需要）
        """
        body = {"name": field_name, "type": field_type}
        if options and field_type == "Select":
            body["options"] = options
        return await self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            json=body,
            user_access_token=user_access_token,
        )

    # ======================== 审批 API ========================

    async def approval_create(
        self,
        approval_code: str,
        form_data: dict | list,
        open_id: str = "",
        title: str = "",
        user_access_token: str = "",
        use_app_token: bool = False,
    ) -> dict:
        """创建审批实例

        Args:
            approval_code: 审批定义唯一编码
            form_data: 表单数据（飞书API要求的数组格式：[{"id":"控件ID","type":"控件类型","value":"值"}, ...]）
            open_id: 审批发起人的用户open_id（应用身份时必填）
            title: 审批实例展示名称（可选）
            user_access_token: 用户访问令牌
            use_app_token: 是否使用应用身份token
        """
        import json as json_mod

        body = {
            "approval_code": approval_code,
            "form": json_mod.dumps(form_data, ensure_ascii=False),
        }
        if open_id:
            body["open_id"] = open_id
        if title:
            body["title"] = title

        return await self._request(
            "POST",
            "/approval/v4/instances",
            json=body,
            user_access_token=user_access_token,
            use_app_token=use_app_token,
        )

    async def approval_get_instance(
        self,
        instance_id: str,
        user_access_token: str = "",
    ) -> dict:
        """获取审批实例详情

        Args:
            instance_id: 审批实例ID
            user_access_token: 用户访问令牌（提供时优先使用用户身份，失败时回退到应用身份）
        """
        result = await self._request(
            "GET",
            f"/approval/v4/instances/{instance_id}",
            user_access_token=user_access_token,
            use_app_token=not user_access_token,
        )

        if result.get("error") and user_access_token:
            logger.warning(f"用户token获取实例详情失败，回退到应用token: {instance_id}")
            return await self._request(
                "GET",
                f"/approval/v4/instances/{instance_id}",
                use_app_token=True,
            )

        return result

    async def approval_approve(
        self,
        instance_id: str,
        action_type: str,
        comment: str = "",
        user_access_token: str = "",
    ) -> dict:
        """审批处理（通过/拒绝）- 使用实例ID

        Args:
            instance_id: 审批实例ID
            action_type: pass/reject
            comment: 审批意见
        """
        # 先尝试用用户token
        result = await self._request(
            "POST",
            f"/approval/v4/instances/{instance_id}/operations",
            json={
                "action_type": action_type,
                "comment": comment,
            },
            user_access_token=user_access_token,
        )

        # 用户token失败时，回退到应用token
        if result.get("error") and user_access_token:
            logger.warning(f"审批操作使用用户token失败，回退到应用token: {instance_id}")
            result = await self._request(
                "POST",
                f"/approval/v4/instances/{instance_id}/operations",
                json={
                    "action_type": action_type,
                    "comment": comment,
                },
                use_app_token=True,
            )

        return result

    async def approval_get_definitions(self, user_access_token: str = "") -> dict:
        """获取审批定义列表

        Args:
            user_access_token: 用户访问令牌（可选，提供时使用用户身份）

        Returns:
            审批定义列表，包含 approval_code、approval_name等信息
        """
        return await self._request(
            "GET",
            "/approval/v4/approvals",
            params={"page_size": 50},
            user_access_token=user_access_token,
            use_app_token=not user_access_token,
        )

    async def approval_get_definition_detail(self, approval_code: str) -> dict:
        """获取审批定义详情（包含表单控件结构）

        使用应用身份token获取审批定义详情（非用户特定操作）。

        Args:
            approval_code: 审批定义码

        Returns:
            审批定义详情，包含表单结构（form_schema）
        """
        return await self._request(
            "GET",
            f"/approval/v4/approvals/{approval_code}",
            use_app_token=True,
        )

    async def approval_get_tasks(
        self,
        topic: int = 1,
        definition_code: str = "",
        page_size: int = 20,
        user_access_token: str = "",
    ) -> dict:
        """获取审批任务列表

        注意：此接口必须使用用户身份token，不支持应用身份。

        Args:
            topic: 任务分组，1=待审批，2=已审批，3=我发起的
            definition_code: 审批定义码过滤
            page_size: 每页数量

        Returns:
            任务列表，包含 task_id、instance_code 等核心标识
        """
        params = {"topic": topic, "page_size": page_size}
        if definition_code:
            params["definition_code"] = definition_code

        return await self._request(
            "GET",
            "/approval/v4/tasks",
            params=params,
            user_access_token=user_access_token,
        )

    async def approval_approve_task(
        self,
        task_id: str,
        instance_code: str,
        action_type: str,
        comment: str = "",
        approval_code: str = "",
        user_id: str = "",
        user_access_token: str = "",
    ) -> dict:
        """审批任务操作（通过/拒绝）- 使用任务ID

        Args:
            task_id: 任务ID
            instance_code: 审批实例编码
            action_type: pass/reject
            comment: 审批意见
            approval_code: 审批定义码（应用身份必须）
            user_id: 当前审批人ID（应用身份必须）
        """
        payload = {
            "task_id": task_id,
            "instance_code": instance_code,
            "comment": comment,
        }
        
        if approval_code:
            payload["approval_code"] = approval_code
        if user_id:
            payload["user_id"] = user_id
        
        if action_type == "pass":
            api_path = "/approval/v4/tasks/approve"
        elif action_type == "reject":
            api_path = "/approval/v4/tasks/reject"
        else:
            return {"code": -1, "msg": f"无效的action_type: {action_type}", "error": True}
        
        result = await self._request(
            "POST",
            api_path,
            json=payload,
            user_access_token=user_access_token,
        )

        if result.get("error") and approval_code and user_id:
            logger.warning(f"用户身份审批失败(code={result.get('code')}), 尝试应用身份")
            return await self._request(
                "POST",
                api_path,
                json=payload,
                use_app_token=True,
            )

        return result

    async def approval_get_user_id(
        self,
        email: str = "",
        mobile: str = "",
        user_access_token: str = "",
    ) -> dict:
        """通过邮箱或手机号获取用户ID

        Args:
            email: 用户邮箱
            mobile: 用户手机号

        Returns:
            用户信息，包含 open_id 等
        """
        params = {}
        if email:
            params["email"] = email
        if mobile:
            params["mobile"] = mobile
        return await self._request(
            "GET",
            "/contact/v3/users/batch_get_id",
            params=params,
            user_access_token=user_access_token,
        )

    # ======================== 群聊 API ========================

    async def group_create(
        self,
        name: str,
        member_ids: list[str],
        description: str = "",
        user_access_token: str = "",
    ) -> dict:
        """创建飞书群聊

        优先使用应用身份创建群聊，确保机器人自动入群（以便发送消息和公告）。
        应用身份失败时回退到用户身份。

        Args:
            name: 群名称
            member_ids: 成员ID列表（open_id）
            description: 群描述
        """
        body = {
            "name": name,
            "user_id_list": member_ids,
            "description": description,
            "chat_mode": "group",  # 指定群模式为group，确保群公告等功能可用
        }
        # 优先使用应用身份（确保机器人自动入群）
        result = await self._request(
            "POST",
            "/im/v1/chats",
            json=body,
            use_app_token=True,
        )
        if result.get("code") == 0:
            return result
        logger.warning(f"应用身份创建群聊失败，尝试用户身份: {result.get('msg', '')}")

        # 回退到用户身份
        if user_access_token:
            result = await self._request(
                "POST",
                "/im/v1/chats",
                json=body,
                user_access_token=user_access_token,
            )
            if result.get("code") == 0:
                return result
            logger.warning(f"用户身份创建群聊也失败: {result.get('msg', '')}")

        return result

    async def add_chat_members(
        self,
        chat_id: str,
        member_ids: list[str],
        user_access_token: str = "",
    ) -> dict:
        """添加群成员

        Args:
            chat_id: 群聊ID
            member_ids: 成员ID列表（open_id）
        """
        return await self._request(
            "POST",
            f"/im/v1/chats/{chat_id}/members",
            json={"id_list": member_ids},
            user_access_token=user_access_token,
        )

    async def set_chat_announcement(
        self,
        chat_id: str,
        content: str,
        user_access_token: str = "",
    ) -> dict:
        """设置群公告

        Args:
            chat_id: 群聊ID
            content: 公告内容（纯文本）
        """
        # 先获取当前公告信息以获取revision
        get_result = await self._request(
            "GET",
            f"/im/v1/chats/{chat_id}/announcement",
            use_app_token=True,
        )
        revision = ""
        if get_result.get("code") == 0:
            data = get_result.get("data", {})
            revision = data.get("revision", "") or data.get("announcement", {}).get("revision", "")
            logger.debug(f"获取到公告revision: {revision}")
        else:
            logger.warning(f"获取公告信息失败: {get_result.get('msg', '')}")

        body = {"content": content}
        if revision:
            body["revision"] = revision
        
        return await self._request(
            "PATCH",
            f"/im/v1/chats/{chat_id}/announcement",
            json=body,
            use_app_token=True,
        )

    async def send_chat_message(
        self,
        chat_id: str,
        content: str,
        msg_type: str = "text",
    ) -> dict:
        """向群聊发送消息

        Args:
            chat_id: 群聊ID
            content: 消息内容
            msg_type: 消息类型
        """
        return await self.send_message(
            receive_id=chat_id,
            content=content,
            msg_type=msg_type,
            receive_id_type="chat_id",
            use_app_token=True,
        )

    async def send_chat_card(
        self,
        chat_id: str,
        card: dict,
    ) -> dict:
        """向群聊发送卡片消息

        Args:
            chat_id: 群聊ID
            card: 卡片内容
        """
        import json as json_mod
        if isinstance(card, dict) and card.get("schema") == "2.0":
            content = json_mod.dumps(card)
        else:
            content = json_mod.dumps({"config": {"wide_screen_mode": True}, "header": {}, "elements": card} if isinstance(card, list) else card)
        return await self.send_message(
            receive_id=chat_id,
            content=content,
            msg_type="interactive",
            receive_id_type="chat_id",
            use_app_token=True,
        )

    # ======================== 权限管理 API ========================

    async def set_bitable_permission(
        self,
        app_token: str,
        member_ids: list[str],
        perm: str = "full_access",
    ) -> dict:
        """设置多维表格权限（通过Drive API）

        Args:
            app_token: 多维表格app_token
            member_ids: 成员open_id列表
            perm: 权限级别（full_access/edit/view）
        """
        import json as json_mod
        results = []
        for member_id in member_ids:
            body = {
                "member_type": "openid",
                "member_id": member_id,
                "perm": perm,
                "type": "user",
                "need_notification": False,
            }
            result = await self._request(
                "POST",
                f"/drive/v1/permissions/{app_token}/members?type=bitable",
                json=body,
                use_app_token=True,
            )
            results.append(result)
        return {"code": 0, "results": results}

    async def create_bitable_with_template(
        self,
        app_name: str,
        table_name: str,
        fields: list[dict],
        records: list[dict] = None,
        user_access_token: str = "",
    ) -> dict:
        """使用模板创建多维表格并写入记录

        Args:
            app_name: 应用名称
            table_name: 表名
            fields: 字段定义列表 [{"field_name": "需求", "type": 1, "property": {...}}, ...]
            records: 记录数据列表 [{"fields": {"需求": "物料准备", "优先级": "P1", "状态": "未开始"}}, ...]
            user_access_token: 用户访问令牌

        Returns:
            {"ok": True, "app_token": "...", "table_id": "...", "url": "..."}
        """
        # Step 1: 创建多维表格应用
        result = await self.bitable_create_app(app_name, user_access_token=user_access_token)
        if result.get("code") != 0:
            return {"ok": False, "error": f"创建多维表格应用失败: {result}", "app_token": ""}

        app_token = result.get("data", {}).get("app", {}).get("app_token", "")
        if not app_token:
            return {"ok": False, "error": "未获取到app_token", "app_token": ""}

        # Step 2: 创建数据表（先创建第一个字段）
        primary_field = fields[0] if fields else {"field_name": "索引", "type": 1}
        table_result = await self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables",
            json={
                "table": {
                    "name": table_name,
                    "default_view_name": "默认视图",
                    "fields": [{"field_name": primary_field["field_name"], "type": primary_field["type"]}],
                }
            },
            user_access_token=user_access_token,
        )
        if table_result.get("code") != 0:
            return {"ok": False, "error": f"创建数据表失败: {table_result}", "app_token": app_token}

        table_id = table_result.get("data", {}).get("table_id", "")

        # Step 3: 添加剩余字段
        for field in fields[1:]:
            body = {"field_name": field["field_name"], "type": field["type"]}
            if "property" in field:
                body["property"] = field["property"]
            await self._request(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
                json=body,
                user_access_token=user_access_token,
            )

        # Step 4: 写入记录
        if records:
            await self.bitable_add_records(
                app_token=app_token,
                table_id=table_id,
                records=records,
                user_access_token=user_access_token,
            )

        # Step 5: 删除创建应用时自动生成的默认空白表
        await self._delete_default_tables(app_token, table_id, user_access_token)

        return {
            "ok": True,
            "app_token": app_token,
            "table_id": table_id,
            "url": f"https://{self.TENANT_DOMAIN}/base/{app_token}",
        }

    async def _delete_default_tables(
        self,
        app_token: str,
        keep_table_id: str,
        user_access_token: str = "",
    ):
        """删除多维表格中除指定表之外的所有默认空白表"""
        try:
            list_result = await self.bitable_list_tables(
                app_token, user_access_token=user_access_token
            )
            if list_result.get("code") != 0:
                logger.warning(f"获取表格列表失败: {list_result.get('msg', '')}")
                return

            tables = list_result.get("data", {}).get("items", [])
            for table in tables:
                t_id = table.get("table_id", "")
                if t_id and t_id != keep_table_id:
                    del_result = await self.bitable_delete_table(
                        app_token, t_id, user_access_token=user_access_token
                    )
                    if del_result.get("code") == 0:
                        logger.info(f"已删除默认空白表: {table.get('name', '')} (table_id: {t_id})")
                    else:
                        logger.warning(f"删除默认表失败: {table.get('name', '')} - {del_result.get('msg', '')}")
        except Exception as e:
            logger.warning(f"清理默认表时出错: {e}")

    async def bitable_delete_table(
        self,
        app_token: str,
        table_id: str,
        user_access_token: str = "",
    ) -> dict:
        """删除多维表格中的数据表

        Args:
            app_token: 多维表格应用token
            table_id: 表格ID
        """
        return await self._request(
            "DELETE",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}",
            user_access_token=user_access_token,
        )

    async def bitable_create_view(
        self,
        app_token: str,
        table_id: str,
        view_name: str,
        view_type: str,
        user_access_token: str = "",
    ) -> dict:
        """在多维表格中创建视图

        Args:
            app_token: 多维表格应用token
            table_id: 表格ID
            view_name: 视图名称
            view_type: 视图类型 (grid/form/kanban/gantt/gallery/calendar)
        """
        return await self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/views",
            json={
                "view_type": view_type,
                "view_name": view_name,
            },
            user_access_token=user_access_token,
        )

    async def bitable_create_form_view(
        self,
        app_token: str,
        table_id: str,
        view_name: str = "表单",
        user_access_token: str = "",
    ) -> dict:
        """在多维表格中创建表单视图（让表格看起来像问卷）

        Args:
            app_token: 多维表格应用token
            table_id: 表格ID
            view_name: 视图名称
        """
        return await self.bitable_create_view(
            app_token, table_id, view_name, "form", user_access_token=user_access_token
        )

    async def bitable_delete_field(
        self,
        app_token: str,
        table_id: str,
        field_id: str,
        user_access_token: str = "",
    ) -> dict:
        """删除多维表格字段

        Args:
            app_token: 多维表格应用token
            table_id: 表格ID
            field_id: 字段ID
        """
        return await self._request(
            "DELETE",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}",
            user_access_token=user_access_token,
        )

    # ======================== 卡片消息 API ========================

    async def send_card_message(
        self,
        receive_id: str,
        card: dict,
        user_access_token: str = "",
        use_app_token: bool = False,
    ) -> dict:
        """发送飞书卡片消息

        Args:
            receive_id: 接收者ID（open_id/chat_id）
            card: 卡片内容（JSON格式，支持完整JSON 2.0或elements数组）
            user_access_token: 用户访问令牌
            use_app_token: 是否使用应用身份token（tenant_access_token）发送
        """
        if isinstance(card, dict) and card.get("schema") == "2.0":
            content = json.dumps(card)
        elif isinstance(card, list):
            content = json.dumps({"config": {"wide_screen_mode": True}, "header": {}, "elements": card})
        else:
            content = json.dumps({"config": {"wide_screen_mode": True}, "header": {}, "elements": card})
        return await self._request(
            "POST",
            f"/im/v1/messages?receive_id_type=open_id",
            json={
                "receive_id": receive_id,
                "content": content,
                "msg_type": "interactive",
            },
            user_access_token=user_access_token if not use_app_token else "",
            use_app_token=use_app_token,
        )

    # ======================== Webhook 签名验证 ========================

    def verify_webhook_signature(
        self,
        timestamp: str,
        nonce: str,
        signature: str,
        body: str,
    ) -> bool:
        """验证飞书Webhook签名

        Args:
            timestamp: 时间戳
            nonce: 随机字符串
            signature: 签名
            body: 请求体
        """
        try:
            import hmac
            import hashlib
            import base64

            string_to_sign = f"{timestamp}{nonce}{body}"
            hmac_obj = hmac.new(
                self.app_secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha256,
            )
            expected_signature = base64.b64encode(hmac_obj.digest()).decode("utf-8")
            return expected_signature == signature
        except Exception as e:
            logger.error(f"验证Webhook签名失败: {e}")
            return False


feishu_client = FeishuClient()
