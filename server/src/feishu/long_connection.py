"""飞书长连接客户端

使用飞书官方 SDK (lark-oapi) 建立 WebSocket 长连接，订阅事件回调。
无需公网地址，飞书主动推送事件到客户端。
"""

import logging
import json
import threading
from typing import Optional

import httpx

from lark_oapi.ws import Client
from lark_oapi.event.dispatcher_handler import (
    EventDispatcherHandlerBuilder,
)

logger = logging.getLogger(__name__)

_task_cache = {}


def cache_task_info(task_id: str, assignee_name: str, initiator_id: str, user_access_token: str = "", task_summary: str = ""):
    _task_cache[task_id] = {
        "assignee_name": assignee_name,
        "initiator_id": initiator_id,
        "user_access_token": user_access_token,
        "task_summary": task_summary,
    }


def get_cached_task_info(task_id: str):
    return _task_cache.get(task_id, {})


def clear_task_cache(task_id: str):
    if task_id in _task_cache:
        del _task_cache[task_id]


class FeishuLongConnectionClient:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._client: Optional[Client] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self):
        """启动长连接客户端（在独立线程中运行）"""
        if self._running:
            logger.warning("长连接客户端已在运行")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("飞书长连接客户端已启动")

    def stop(self):
        """停止长连接客户端"""
        self._running = False
        if self._client:
            self._client.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("飞书长连接客户端已停止")

    def _run(self):
        """在独立线程中运行长连接"""
        try:
            event_handler = EventDispatcherHandlerBuilder("", "") \
                .register_p2_card_action_trigger(self._on_card_action) \
                .register_p2_im_message_receive_v1(self._on_message_receive) \
                .register_p2_im_message_message_read_v1(self._on_message_read) \
                .build()

            self._client = Client(
                app_id=self.app_id,
                app_secret=self.app_secret,
                event_handler=event_handler,
                domain="https://open.feishu.cn",
                auto_reconnect=True,
            )

            logger.info("飞书长连接客户端初始化完成，正在连接...")
            self._client.start()
        except Exception as e:
            logger.error(f"长连接客户端运行异常: {e}", exc_info=True)
            self._running = False

    def _on_message_receive(self, event):
        """处理消息接收事件"""
        logger.info(f"收到消息事件: {event.header.event_type}")

    def _on_message_read(self, event):
        """处理消息已读事件"""
        logger.info(f"收到消息已读事件: {event.header.event_type}")

    def _on_card_action(self, event):
        """处理卡片交互事件（核心）

        使用同步 httpx 调用处理动作，在3秒内返回响应给飞书。
        """
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
            CallBackToast,
            CallBackCard,
        )

        logger.info(f"收到卡片交互事件: {event.header.event_type}")
        try:
            action_value = event.event.action.value
            logger.info(f"卡片动作原始值 (type={type(action_value).__name__}): {action_value}")
            # 容错：action_value 可能为 None、dict 或 JSON 字符串
            if action_value is None:
                action_value = {}
            elif isinstance(action_value, str):
                try:
                    action_value = json.loads(action_value)
                except (json.JSONDecodeError, TypeError):
                    action_value = {}
            elif not isinstance(action_value, dict):
                action_value = {}
            form_value = getattr(event.event.action, "form_value", None)
            if form_value:
                if isinstance(form_value, str):
                    try:
                        form_value = json.loads(form_value)
                    except (json.JSONDecodeError, TypeError):
                        pass
                action_value["form_value"] = form_value
            # 提取操作者 open_id（谁点击了按钮），用于获取正确的用户 token
            operator_open_id = getattr(event.event.operator, "open_id", "")
            if operator_open_id:
                action_value["operator_open_id"] = operator_open_id
            logger.info(f"卡片动作值(处理后): {action_value}")

            toast_type = "info"
            toast_content = "处理完成"
            card_data = None

            result = _process_card_action_sync(action_value)

            card_data = None
            if result and isinstance(result, dict):
                toast = result.get("toast")
                if toast:
                    toast_type = toast.get("type", "info")
                    toast_content = toast.get("content", "处理完成")

                card_info = result.get("card")
                if card_info:
                    card_type = card_info.get("type", "replace")
                    card_data = card_info.get("data", card_info)
            else:
                toast_type = "success"
                toast_content = "操作已完成"

            response = P2CardActionTriggerResponse()
            response.toast = CallBackToast({
                "type": toast_type,
                "content": toast_content,
            })
            if card_data:
                response.card = CallBackCard({
                    "type": card_type,
                    "data": card_data,
                })

            logger.info(f"返回响应: toast={toast_type}, content={toast_content}")
            return response

        except Exception as e:
            logger.error(f"处理卡片交互事件异常: {e}", exc_info=True)
            response = P2CardActionTriggerResponse()
            response.toast = CallBackToast({
                "type": "error",
                "content": "处理失败",
            })
            return response


def _get_tenant_token_sync(app_id: str, app_secret: str) -> str:
    """同步获取 tenant_access_token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    with httpx.Client(timeout=10) as client:
        resp = client.post(url, json={"app_id": app_id, "app_secret": app_secret})
        resp.raise_for_status()
        data = resp.json()
    return data.get("tenant_access_token", "")


def _call_remind_action_api(action_value: dict, user_access_token: str = "") -> dict:
    """通过调用服务端 /api/v1/remind/action 处理卡片动作（使用用户token）

    飞书任务API（complete/update）需要用户access_token，
    tenant_token没有权限操作用户任务。
    """
    try:
        token_prefix = user_access_token[:20] if user_access_token else "EMPTY"
        task_id = action_value.get("task_id", "")
        logger.info(f"_call_remind_action_api: task_id={task_id}, action={action_value.get('action')}, token_prefix='{token_prefix}'")
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                "http://localhost:8000/api/v1/remind/action",
                json={
                    "action_data": action_value,
                    "user_access_token": user_access_token,
                },
                headers={"Authorization": "Bearer lanshan-dev-token"},
            )
        result = resp.json()
        logger.info(f"remind/action API 返回: {result}")
        return result
    except Exception as e:
        logger.error(f"调用 remind/action API 失败: {e}")
        return {"ok": False, "toast": {"type": "error", "content": f"操作失败: {str(e)[:30]}"}}


def _call_remind_delay_api(task_id: str, new_due_date: str, user_access_token: str = "") -> bool:
    """通过调用服务端 /api/v1/remind/delay 更新任务截止日期（使用用户token）

    飞书任务API需要用户access_token，tenant_token没有权限。
    """
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                "http://localhost:8000/api/v1/remind/delay",
                json={
                    "action_data": {"task_id": task_id, "assignee_id": ""},
                    "form_data": {
                        "delay_reason": "发起人审批通过",
                        "new_due_date": new_due_date,
                        "delay_note": "",
                    },
                    "user_access_token": user_access_token,
                },
                headers={"Authorization": "Bearer lanshan-dev-token"},
            )
        result = resp.json()
        logger.info(f"remind/delay API 返回: {result}")
        return result.get("ok", False)
    except Exception as e:
        logger.error(f"调用 remind/delay API 失败: {e}")
        return False


def _send_card_message_sync(token: str, receive_id: str, card: dict):
    """同步发送飞书卡片消息"""
    try:
        url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
        content = json.dumps(card)
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, headers={"Authorization": f"Bearer {token}"}, json={
                "receive_id": receive_id,
                "msg_type": "interactive",
                "content": content,
            })
        data = resp.json()
        if data.get("code") == 0:
            logger.info(f"卡片消息已发送给 {receive_id}")
        else:
            logger.warning(f"发送卡片消息失败: {data.get('msg', '')}")
    except Exception as e:
        logger.error(f"发送卡片消息异常: {e}")


def _get_user_access_token_sync(open_id: str) -> str:
    """同步获取用户的 access_token（从内存缓存）"""
    if not open_id:
        return ""
    try:
        from server.src.services.auth_service import _user_tokens
        token_info = _user_tokens.get(open_id, {})
        token = token_info.get("access_token", "")
        if token:
            logger.info(f"从缓存获取用户token成功: open_id={open_id}, token_prefix={token[:20]}...")
        else:
            logger.warning(f"缓存中未找到用户token: open_id={open_id}")
        return token
    except Exception as e:
        logger.error(f"获取用户token失败: open_id={open_id}, error={e}")
        return ""


def _process_card_action_sync(action_value: dict) -> dict:
    """同步处理卡片动作

    直接使用同步 httpx 调用飞书API，避免 asyncio event loop 冲突。
    """
    if not action_value or not isinstance(action_value, dict):
        logger.error(f"卡片动作参数不完整: {action_value}")
        return {"ok": False, "toast": {"type": "error", "content": "参数不完整"}}
    
    task_id = action_value.get("task_id", "")
    action = action_value.get("action", "")
    assignee_id = action_value.get("assignee_id", "")
    initiator_id = action_value.get("initiator_id", "")
    assignee_name = action_value.get("assignee_name", "")
    task_summary = action_value.get("task_summary", "")
    operator_open_id = action_value.get("operator_open_id", "")

    if not task_id or not action:
        logger.error(f"卡片动作参数不完整: task_id={task_id}, action={action}")
        return {"ok": False, "toast": {"type": "error", "content": "参数不完整"}}

    cached_info = get_cached_task_info(task_id)
    if cached_info:
        if not assignee_name:
            assignee_name = cached_info.get("assignee_name", "")
        if not initiator_id:
            initiator_id = cached_info.get("initiator_id", "")
        if not task_summary:
            task_summary = cached_info.get("task_summary", "")

    cached_user_access_token = cached_info.get("user_access_token", "") if cached_info else ""

    # 优先使用操作者的 token（点击按钮的人），回退到缓存的 token
    operator_token = _get_user_access_token_sync(operator_open_id) if operator_open_id else ""
    effective_token = operator_token or cached_user_access_token

    logger.info(f"同步处理卡片动作: task_id={task_id}, action={action}, assignee_name={assignee_name}, initiator_id={initiator_id}, operator_open_id={operator_open_id}, has_operator_token={bool(operator_token)}")

    from .client import feishu_client
    token = _get_tenant_token_sync(feishu_client.app_id, feishu_client.app_secret)

    # --- 已完成：通过服务端 API 使用操作者 token 完成任务 ---
    if action == "completed":
        result = _call_remind_action_api(action_value, effective_token)
        if result.get("ok"):
            return {"ok": True, "toast": {"type": "success", "content": "任务已完成"}}
        else:
            error_msg = result.get("message", result.get("error", "操作失败"))[:30]
            return {"ok": False, "toast": {"type": "error", "content": f"完成失败: {error_msg}"}}

    # --- 求助 ---
    elif action == "help":
        if not assignee_name:
            assignee_name = _get_user_name_sync(token, assignee_id)
        if not initiator_id:
            logger.error(f"求助失败: initiator_id为空，task_id={task_id}")
            return {"ok": False, "toast": {"type": "error", "content": "无法发送求助：未找到任务发起人"}}
        _send_text_message_sync(token, initiator_id, f"{assignee_name}任务有困难，请求协助")
        logger.info(f"求助消息已发送给 {initiator_id}")
        return {"ok": True, "toast": {"type": "success", "content": "求助请求已发送给任务发起人"}}

    # --- 申请延期（发送表单为新消息，原卡片不消失） ---
    elif action == "delay":
        card = _build_delay_form_card(task_id, initiator_id, assignee_id, assignee_name, task_summary)
        # 作为独立消息发送，不替换原催办卡片
        _send_card_message_sync(token, assignee_id, card)
        logger.info("延期申请表单已发送（独立消息，原卡片保留）")
        return {"ok": True, "toast": {"type": "info", "content": "请在弹出的延期申请表单中填写"}}

    # --- 提交延期申请 → 发送审批卡片给发起人 ---
    elif action == "delay_submit":
        # 飞书 form submit 的 form_value 可能在 event.event.action.form_value
        # 也可能在被注入到 action_value["form_value"] 中（由 _on_card_action 处理）
        form_value = action_value.get("form_value") or {}
        if isinstance(form_value, str):
            try:
                form_value = json.loads(form_value)
            except:
                form_value = {}

        delay_time = form_value.get("delay_time", "")
        delay_reason = form_value.get("delay_reason", "")
        delay_note = form_value.get("delay_note", "")

        # 容错：如果 form_value 为空，尝试直接从 action_value 获取
        if not delay_time:
            delay_time = action_value.get("delay_time", "")
        if not delay_reason:
            delay_reason = action_value.get("delay_reason", "")

        logger.info(f"[延期诊断] delay_submit: form_value_keys={list(form_value.keys()) if form_value else 'EMPTY'}, delay_time='{delay_time}', delay_reason='{delay_reason}'")

        if delay_time and "+" in delay_time:
            delay_time = delay_time.split("+")[0].strip()
        elif delay_time and "T" in delay_time:
            delay_time = delay_time.split("T")[0].strip()

        if not delay_time:
            logger.error(f"延期申请缺少延期时间: form_value={form_value}")
            return {"ok": False, "toast": {"type": "error", "content": "请选择延期日期"}}

        if not assignee_name:
            assignee_name = form_value.get("assignee_name", "")
        if not assignee_name:
            assignee_name = _get_user_name_sync(token, assignee_id)
        if not initiator_id:
            logger.error(f"延期申请失败: initiator_id为空，task_id={task_id}")
            return {"ok": False, "toast": {"type": "error", "content": "无法发送延期申请：未找到任务发起人"}}

        # 发送审批卡片给发起人（而不是直接更新任务）
        approval_card = _build_delay_approval_card(
            task_id=task_id,
            initiator_id=initiator_id,
            assignee_id=assignee_id,
            assignee_name=assignee_name,
            delay_time=delay_time,
            delay_reason=delay_reason,
            delay_note=delay_note,
            task_summary=task_summary,
        )
        _send_card_message_sync(token, initiator_id, approval_card)
        logger.info(f"延期审批卡片已发送给 {initiator_id}")
        return {"ok": True, "toast": {"type": "success", "content": "延期申请已提交，等待发起人审批"}}

    # --- 发起人同意延期 ---
    elif action == "approve_delay":
        delay_time = action_value.get("delay_time", "")
        delay_reason = action_value.get("delay_reason", "")
        logger.info(f"[延期诊断] approve_delay: action_value={action_value}, delay_time='{delay_time}', effective_token_prefix='{effective_token[:20] if effective_token else 'EMPTY'}'")
        if not assignee_name:
            assignee_name = _get_user_name_sync(token, assignee_id)

        # 通过服务端 API 更新任务截止日期（使用操作者 token）
        task_update_ok = _call_remind_delay_api(task_id, delay_time, effective_token) if delay_time else False

        # 通知被催办人：延期已同意
        if assignee_id:
            _send_text_message_sync(
                token, assignee_id,
                f"你的延期申请已通过\n任务: {task_id}\n延期至: {delay_time}"
            )

        # 返回更新的卡片（显示已同意）
        result_card = _build_approval_result_card(
            assignee_name=assignee_name,
            delay_time=delay_time,
            delay_reason=delay_reason,
            approved=True,
            task_summary=task_summary,
        )
        if task_update_ok:
            return {"ok": True, "card": {"type": "raw", "data": result_card}, "toast": {"type": "success", "content": "已同意延期申请"}}
        else:
            return {"ok": True, "card": {"type": "raw", "data": result_card}, "toast": {"type": "warning", "content": "已同意，但任务更新失败"}}

    # --- 发起人拒绝延期 ---
    elif action == "reject_delay":
        delay_time = action_value.get("delay_time", "")
        delay_reason = action_value.get("delay_reason", "")
        if not assignee_name:
            assignee_name = _get_user_name_sync(token, assignee_id)

        # 通知被催办人：延期已拒绝
        if assignee_id:
            _send_text_message_sync(
                token, assignee_id,
                f"你的延期申请已被拒绝\n任务: {task_id}\n申请延期至: {delay_time}"
            )

        # 返回更新的卡片（显示已拒绝）
        result_card = _build_approval_result_card(
            assignee_name=assignee_name,
            delay_time=delay_time,
            delay_reason=delay_reason,
            approved=False,
            task_summary=task_summary,
        )
        return {"ok": True, "card": {"type": "raw", "data": result_card}, "toast": {"type": "success", "content": "已拒绝延期申请"}}

    else:
        return {"ok": True, "toast": {"type": "info", "content": f"已收到操作: {action}"}}


def _get_user_name_sync(token: str, open_id: str) -> str:
    """同步获取用户姓名"""
    if not open_id:
        return "未知用户"
    try:
        url = f"https://open.feishu.cn/open-apis/contact/v3/users/{open_id}"
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {token}"}, params={"user_id_type": "open_id"})
        data = resp.json()
        if data.get("code") == 0:
            return data.get("data", {}).get("user", {}).get("name", "未知用户")
        logger.warning(f"获取用户名失败: {data.get('msg', '')}")
    except Exception as e:
        logger.error(f"获取用户名异常: {e}")
    return "未知用户"


def _send_text_message_sync(token: str, receive_id: str, text: str):
    """同步发送文本消息"""
    try:
        url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
        content = json.dumps({"text": text})
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, headers={"Authorization": f"Bearer {token}"}, json={
                "receive_id": receive_id,
                "msg_type": "text",
                "content": content,
            })
        data = resp.json()
        if data.get("code") == 0:
            logger.info(f"消息已发送给 {receive_id}")
        else:
            logger.warning(f"发送消息失败: {data.get('msg', '')}")
    except Exception as e:
        logger.error(f"发送消息异常: {e}")


def _build_delay_form_card(task_id: str, initiator_id: str, assignee_id: str, assignee_name: str = "", task_summary: str = "") -> dict:
    """构建延期申请表单卡片（飞书卡片 JSON 2.0）"""
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "延期申请"},
            "template": "blue",
        },
        "body": {
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "请填写延期申请表单，提交后将通知任务发起人",
                    },
                },
                {
                    "tag": "form",
                    "name": "delay_form",
                    "elements": [
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": "**延期时间**",
                            },
                        },
                        {
                            "tag": "date_picker",
                            "name": "delay_time",
                            "placeholder": {
                                "tag": "plain_text",
                                "content": "选择延期日期",
                            },
                        },
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": "**延期理由**",
                            },
                        },
                        {
                            "tag": "input",
                            "name": "delay_reason",
                            "required": True,
                            "placeholder": {
                                "tag": "plain_text",
                                "content": "请输入延期理由",
                            },
                        },
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": "**备注（可选）**",
                            },
                        },
                        {
                            "tag": "input",
                            "name": "delay_note",
                            "placeholder": {
                                "tag": "plain_text",
                                "content": "选填",
                            },
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "提交延期申请"},
                            "type": "primary",
                            "form_action_type": "submit",
                            "behaviors": [
                                {
                                    "type": "callback",
                                    "value": {
                                        "task_id": task_id,
                                        "action": "delay_submit",
                                        "initiator_id": initiator_id,
                                        "assignee_id": assignee_id,
                                        "assignee_name": assignee_name,
                                        "task_summary": task_summary,
                                    },
                                },
                            ],
                        },
                    ],
                },
            ],
        },
    }


def _build_delay_approval_card(
    task_id: str,
    initiator_id: str,
    assignee_id: str,
    assignee_name: str,
    delay_time: str,
    delay_reason: str,
    delay_note: str = "",
    task_summary: str = "",
) -> dict:
    """构建延期审批卡片（发送给发起人审批）"""
    info_text = f"**{assignee_name}** 申请任务延期\n"
    if task_summary:
        info_text += f"任务: **{task_summary}**\n"
    info_text += f"\n延期至: **{delay_time}**\n"
    info_text += f"理由: {delay_reason}"
    if delay_note:
        info_text += f"\n备注: {delay_note}"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "延期审批"},
            "template": "orange",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": info_text,
                },
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "同意延期"},
                        "type": "primary",
                        "value": {
                            "task_id": task_id,
                            "action": "approve_delay",
                            "initiator_id": initiator_id,
                            "assignee_id": assignee_id,
                            "assignee_name": assignee_name,
                            "delay_time": delay_time,
                            "delay_reason": delay_reason,
                            "delay_note": delay_note,
                            "task_summary": task_summary,
                        },
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "拒绝"},
                        "type": "danger",
                        "value": {
                            "task_id": task_id,
                            "action": "reject_delay",
                            "initiator_id": initiator_id,
                            "assignee_id": assignee_id,
                            "assignee_name": assignee_name,
                            "delay_time": delay_time,
                            "delay_reason": delay_reason,
                            "delay_note": delay_note,
                            "task_summary": task_summary,
                        },
                    },
                ],
            },
        ],
    }


def _build_approval_result_card(
    assignee_name: str,
    delay_time: str,
    delay_reason: str,
    approved: bool,
    task_summary: str = "",
) -> dict:
    """构建审批结果卡片（替换审批卡片，展示最终结果）"""
    status = "已同意" if approved else "已拒绝"
    color = "green" if approved else "red"

    info_text = f"**{assignee_name}** 的延期申请 **{status}**\n"
    if task_summary:
        info_text += f"任务: **{task_summary}**\n"
    info_text += f"\n延期至: **{delay_time}**\n"
    info_text += f"理由: {delay_reason}"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"延期审批 - {status}"},
            "template": color,
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": info_text,
                },
            },
        ],
    }


long_connection_client: Optional[FeishuLongConnectionClient] = None


def init_long_connection(app_id: str, app_secret: str):
    """初始化长连接客户端"""
    global long_connection_client
    if long_connection_client is None:
        long_connection_client = FeishuLongConnectionClient(app_id, app_secret)
    return long_connection_client


def start_long_connection():
    """启动长连接客户端"""
    from .client import feishu_client
    if not feishu_client.app_id or not feishu_client.app_secret:
        logger.error("飞书应用凭证未配置")
        return False

    init_long_connection(feishu_client.app_id, feishu_client.app_secret)
    long_connection_client.start()
    return True


def stop_long_connection():
    """停止长连接客户端"""
    if long_connection_client:
        long_connection_client.stop()
