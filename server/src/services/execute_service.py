"""AI任务拆解引擎服务

处理 /execute 指令的业务逻辑：
Step 1: AI拆解任务 → 显示任务表格 → 询问用户确认
Step 2: 创建群聊 → 拉相关人士入群
Step 3: 创建多维表格 → 写入任务记录 → 分享到群
Step 4: AI生成活动草案 → 发布到群公告
Step 5: (选做) 创建问卷 → AI分析字段 → 创建问卷
"""

import logging
import uuid
import json as json_mod
from datetime import datetime

from server.src.ai.llm import llm_engine
from server.src.ai.prompt import (
    TASK_DECOMPOSE_PROMPT,
    ACTIVITY_DRAFT_PROMPT,
    SURVEY_FIELDS_PROMPT,
    parse_ai_json,
)
from server.src.feishu.client import feishu_client

logger = logging.getLogger("lanshan-server.execute_service")

# 模板字段定义（与bitable_template/execute_template.py保持一致）
TEMPLATE_FIELDS = [
    {
        "field_name": "需求",
        "type": 1,  # Text
        "ui_type": "Text",
        "is_primary": True,
    },
    {
        "field_name": "优先级",
        "type": 3,  # SingleSelect
        "ui_type": "SingleSelect",
        "property": {
            "options": [
                {"name": "P0", "color": 11},
                {"name": "P1", "color": 1},
                {"name": "P2", "color": 2},
                {"name": "P3", "color": 9},
            ]
        },
    },
    {
        "field_name": "状态",
        "type": 3,  # SingleSelect
        "ui_type": "SingleSelect",
        "property": {
            "options": [
                {"name": "未开始", "color": 18},
                {"name": "进行中", "color": 13},
                {"name": "已完成", "color": 15},
            ]
        },
    },
    {
        "field_name": "开始时间",
        "type": 5,  # DateTime
        "ui_type": "DateTime",
        "property": {
            "auto_fill": False,
            "date_formatter": "yyyy/MM/dd",
        },
    },
    {
        "field_name": "截止时间",
        "type": 5,  # DateTime
        "ui_type": "DateTime",
        "property": {
            "auto_fill": False,
            "date_formatter": "yyyy/MM/dd",
        },
    },
    {
        "field_name": "负责人员",
        "type": 11,  # User
        "ui_type": "User",
        "property": {
            "multiple": True,
        },
    },
]

EXECUTION_STATES = {
    "pending": "待确认",
    "confirmed": "已确认",
    "running": "执行中",
    "completed": "完成",
    "failed": "异常",
    "paused": "暂停",
    "cancelled": "终止",
}


class ExecutionPlan:
    """执行计划"""

    def __init__(self, goal: str, plan_data: dict):
        self.execution_id = str(uuid.uuid4())
        self.goal = goal
        self.plan_data = plan_data
        self.status = "pending"
        self.created_at = datetime.now()
        self.current_step = 0
        self.results = []
        self.created_resources = []  # 追踪已创建资源，用于回滚

    def confirm(self):
        if self.status != "pending":
            raise ValueError(f"只有待确认的计划才能确认，当前状态: {self.status}")
        self.status = "confirmed"
        logger.info(f"执行计划已确认: {self.execution_id}")

    def start(self):
        if self.status != "confirmed":
            raise ValueError("执行计划必须先确认")
        self.status = "running"
        logger.info(f"执行计划开始执行: {self.execution_id}")

    def pause(self):
        if self.status == "running":
            self.status = "paused"
            logger.info(f"执行计划已暂停: {self.execution_id}")

    def resume(self):
        if self.status == "paused":
            self.status = "running"
            logger.info(f"执行计划已继续: {self.execution_id}")

    def cancel(self):
        if self.status in ("completed", "failed", "cancelled"):
            raise ValueError(f"计划已是终态({self.status})，无法终止")
        self.status = "cancelled"
        logger.info(f"执行计划已终止: {self.execution_id}")

    def add_resource(self, resource_type: str, resource_id: str, resource_name: str = ""):
        self.created_resources.append({
            "type": resource_type,
            "id": resource_id,
            "name": resource_name,
        })

    def complete_step(self, step_name: str, result: dict):
        self.results.append({"step": step_name, **result})
        self.current_step += 1

    def fail(self, error: str):
        self.status = "failed"
        self.results.append({"error": error, "step": f"step_{self.current_step}"})


# 存储执行计划（内存）
_execution_plans = {}


async def decompose_task(goal: str) -> dict:
    """Step 1: AI拆解目标为结构化任务列表

    Args:
        goal: 用户输入的目标
    """
    try:
        prompt = TASK_DECOMPOSE_PROMPT.format(goal=goal, today=datetime.now().strftime("%Y-%m-%d"))
        ai_response = await llm_engine.generate(prompt)
        parsed = parse_ai_json(ai_response)

        if not parsed:
            return {"ok": False, "error": "AI无法生成执行计划", "raw_response": ai_response}

        plan = ExecutionPlan(goal, parsed)
        _execution_plans[plan.execution_id] = plan

        tasks = parsed.get("tasks", [])
        goal_name = parsed.get("goal_name", goal)
        has_survey = parsed.get("has_survey", False)

        return {
            "ok": True,
            "message": "任务拆解完成，请确认后执行",
            "execution_id": plan.execution_id,
            "goal": goal,
            "goal_name": goal_name,
            "summary": parsed.get("summary", ""),
            "tasks": tasks,
            "members": parsed.get("members", []),
            "has_survey": has_survey,
            "total_estimated_time": parsed.get("total_estimated_time", 0),
            "notes": parsed.get("notes", []),
            "status": "pending",
            "created_at": plan.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        }

    except Exception as e:
        logger.error(f"任务拆解失败: {e}")
        return {"ok": False, "error": str(e)}


async def execute_plan(
    execution_id: str,
    user_access_token: str = "",
    user_open_id: str = "",
    user_name: str = "",
) -> dict:
    """执行计划：按Step 2-5顺序执行

    Args:
        execution_id: 执行计划ID
        user_access_token: 用户身份token
        user_open_id: 当前用户open_id
        user_name: 客户端提供的用户姓名（优先使用，为空时从飞书API获取）
    """
    try:
        if execution_id not in _execution_plans:
            return {"ok": False, "error": "执行计划不存在"}

        plan = _execution_plans[execution_id]

        if plan.status == "pending":
            plan.confirm()
            plan.start()
        elif plan.status == "paused":
            plan.resume()
        elif plan.status == "running":
            return {"ok": False, "error": "执行计划已在运行中，请勿重复调用"}

        plan_data = plan.plan_data
        goal_name = plan_data.get("goal_name", plan.goal)
        tasks = plan_data.get("tasks", [])
        members = plan_data.get("members", [])
        has_survey = plan_data.get("has_survey", False)

        if not user_access_token:
            logger.warning("未提供user_access_token，部分功能可能受限")

        # ==================== Step 2: 创建群聊 ====================
        chat_id = ""
        member_ids = []
        step2_result = None

        if plan.current_step <= 0:
            step2_result = await _execute_step2_create_chat(
                plan, goal_name, members, user_access_token, user_open_id
            )
            plan.complete_step("创建群聊", step2_result)

            if not step2_result.get("ok"):
                plan.fail("创建群聊失败")
                return _build_failed_response(plan, step2_result)
        else:
            # 从已完成结果中恢复
            step2_result = plan.results[0]
            logger.info(f"Step 2 已完成，跳过。current_step={plan.current_step}")

        chat_id = step2_result.get("chat_id", "")
        member_ids = step2_result.get("member_ids", [])

        # ==================== Step 3: 创建多维表格并写入任务 ====================
        bitable_url = ""
        app_token = ""
        step3_result = None

        if plan.current_step <= 1:
            step3_result = await _execute_step3_create_bitable(
                plan, goal_name, tasks, chat_id, member_ids, user_access_token, user_open_id
            )
            plan.complete_step("创建多维表格", step3_result)

            if not step3_result.get("ok"):
                plan.fail("创建多维表格失败")
                return _build_failed_response(plan, step3_result)
        else:
            step3_result = plan.results[1]
            logger.info(f"Step 3 已完成，跳过。current_step={plan.current_step}")

        bitable_url = step3_result.get("url", "")
        app_token = step3_result.get("app_token", "")

        # ==================== Step 4: 发布群公告 ====================
        # 优先使用客户端提供的姓名，为空时从飞书API获取
        final_user_name = user_name
        if not final_user_name and user_open_id and user_access_token:
            try:
                user_info = await feishu_client.get_user_info(user_open_id, user_access_token)
                if user_info.get("code") == 0:
                    final_user_name = user_info.get("data", {}).get("user", {}).get("name", "")
            except Exception as e:
                logger.warning(f"获取用户姓名失败: {e}")

        step4_result = None
        if plan.current_step <= 2:
            step4_result = await _execute_step4_announcement(
                plan, goal_name, tasks, chat_id, final_user_name
            )
            plan.complete_step("发布群公告", step4_result)

            if not step4_result.get("ok"):
                plan.fail("发布群公告失败")
                return _build_failed_response(plan, step4_result)
        else:
            step4_result = plan.results[2]
            logger.info(f"Step 4 已完成，跳过。current_step={plan.current_step}")

        # ==================== Step 5: 创建问卷（选做） ====================
        survey_result = None
        if has_survey:
            if plan.current_step <= 3:
                step5_result = await _execute_step5_create_survey(
                    plan, goal_name, tasks, chat_id, user_access_token
                )
                plan.complete_step("创建问卷", step5_result)
                survey_result = step5_result
            else:
                survey_result = plan.results[3]
                logger.info(f"Step 5 已完成，跳过。current_step={plan.current_step}")

        plan.status = "completed"

        return {
            "ok": True,
            "message": "执行计划全部完成！",
            "execution_id": execution_id,
            "status": "completed",
            "results": {
                "chat": {
                    "chat_id": chat_id,
                    "name": step2_result.get("chat_name", ""),
                },
                "bitable": {
                    "app_token": app_token,
                    "url": bitable_url,
                    "records_written": len(tasks),
                },
                "announcement": {
                    "published": step4_result.get("ok", False),
                },
                "survey": survey_result,
            },
            "total_steps": 4 + (1 if has_survey else 0),
        }

    except Exception as e:
        logger.error(f"执行计划失败: {e}")
        if execution_id in _execution_plans:
            _execution_plans[execution_id].fail(str(e))
        return {"ok": False, "error": str(e)}


# ==================== Step 2: 创建群聊 ====================

async def _execute_step2_create_chat(
    plan: ExecutionPlan,
    goal_name: str,
    members: list[str],
    user_access_token: str,
    user_open_id: str,
) -> dict:
    """创建群聊并添加成员"""
    chat_name = f"{goal_name}筹备群"

    if not user_access_token:
        return {"ok": False, "error": "缺少用户身份token，无法创建群聊", "chat_name": chat_name}

    try:
        # 搜索成员获取open_id
        member_ids = [user_open_id] if user_open_id else []
        unfound_members = []
        for member_name in members:
            if member_name == "我" or member_name == "自己":
                continue
            try:
                search_result = await feishu_client.search_users(
                    query=member_name,
                    user_access_token=user_access_token,
                )
                if search_result.get("code") == 0:
                    items = search_result.get("data", {}).get("items", [])
                    if items:
                        member_ids.append(items[0].get("open_id", ""))
                    else:
                        unfound_members.append(member_name)
                        logger.warning(f"未找到成员: {member_name}")
                else:
                    unfound_members.append(member_name)
                    logger.warning(f"搜索成员 {member_name} 失败: {search_result.get('msg', '')}")
            except Exception as e:
                unfound_members.append(member_name)
                logger.warning(f"搜索成员 {member_name} 异常: {e}")

        # 去重
        member_ids = list(set(filter(None, member_ids)))

        if len(member_ids) < 2:
            logger.warning(f"群成员不足，仅有 {len(member_ids)} 人")

        result = await feishu_client.group_create(
            name=chat_name,
            member_ids=member_ids,
            description=f"AI自动创建的{goal_name}筹备群",
            user_access_token=user_access_token,
        )

        if result.get("code") != 0:
            return {
                "ok": False,
                "error": f"创建群聊失败: {result.get('msg', '')}",
                "chat_name": chat_name,
                "member_ids": member_ids,
                "unfound_members": unfound_members,
            }

        chat_id = result.get("data", {}).get("chat_id", "")
        plan.add_resource("chat", chat_id, chat_name)

        logger.info(f"群聊创建成功: {chat_name} (chat_id: {chat_id})")
        return {
            "ok": True,
            "chat_id": chat_id,
            "chat_name": chat_name,
            "member_ids": member_ids,
            "member_count": len(member_ids),
            "unfound_members": unfound_members,
            "message": f"群聊 {chat_name} 创建成功，成员: {len(member_ids)}人",
        }

    except Exception as e:
        logger.error(f"创建群聊失败: {e}")
        return {"ok": False, "error": str(e), "chat_name": chat_name}


# ==================== Step 3: 创建多维表格并写入任务 ====================

async def _execute_step3_create_bitable(
    plan: ExecutionPlan,
    goal_name: str,
    tasks: list[dict],
    chat_id: str,
    member_ids: list[str],
    user_access_token: str,
    user_open_id: str,
) -> dict:
    """创建多维表格，写入任务记录，分享到群"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    app_name = f"{goal_name}进度表_{timestamp}"
    table_name = "进度跟踪"

    try:
        # 构建负责人姓名→open_id映射
        assignee_map = {}
        if user_open_id:
            assignee_map["我"] = user_open_id

        # 搜索AI返回的负责人姓名，获取open_id
        for task in tasks:
            assignee = task.get("assignee", "")
            if assignee and assignee != "我" and assignee not in assignee_map:
                try:
                    search_result = await feishu_client.search_users(
                        query=assignee,
                        user_access_token=user_access_token,
                    )
                    if search_result.get("code") == 0:
                        items = search_result.get("data", {}).get("items", [])
                        if items:
                            assignee_map[assignee] = items[0].get("open_id", "")
                            logger.info(f"负责人 {assignee} → open_id: {assignee_map[assignee]}")
                except Exception as e:
                    logger.warning(f"搜索负责人 {assignee} 失败: {e}")

        # 构建记录数据（包含负责人、开始/截止时间）
        records = []
        for task in tasks:
            fields = {
                "需求": task.get("name", ""),
                "优先级": task.get("priority", "P2"),
                "状态": "未开始",
            }

            # 填充负责人员
            assignee = task.get("assignee", "")
            if assignee and assignee in assignee_map:
                fields["负责人员"] = [{"id": assignee_map[assignee]}]

            # 填充开始时间
            start_date = task.get("start_date", "")
            if start_date:
                try:
                    fields["开始时间"] = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
                except (ValueError, TypeError) as e:
                    logger.warning(f"任务「{task.get('name', '')}」开始时间格式无效: {start_date}, {e}")

            # 填充截止时间
            due_date = task.get("due_date", "")
            if due_date:
                try:
                    fields["截止时间"] = int(datetime.strptime(due_date, "%Y-%m-%d").timestamp() * 1000)
                except (ValueError, TypeError) as e:
                    logger.warning(f"任务「{task.get('name', '')}」截止时间格式无效: {due_date}, {e}")

            records.append({"fields": fields})

        # 创建多维表格
        result = await feishu_client.create_bitable_with_template(
            app_name=app_name,
            table_name=table_name,
            fields=TEMPLATE_FIELDS,
            records=records,
            user_access_token=user_access_token,
        )

        if not result.get("ok"):
            return {
                "ok": False,
                "error": result.get("error", "创建多维表格失败"),
                "app_name": app_name,
            }

        app_token = result["app_token"]
        bitable_url = result["url"]
        plan.add_resource("bitable", app_token, app_name)

        # 设置权限：群成员可编辑
        if member_ids:
            try:
                await feishu_client.set_bitable_permission(
                    app_token=app_token,
                    member_ids=member_ids,
                    perm="full_access",
                )
                logger.info(f"多维表格权限已设置: {len(member_ids)}人可编辑")
            except Exception as e:
                logger.warning(f"设置多维表格权限失败: {e}")

        # 向群聊发送链接
        if chat_id:
            try:
                card = _build_bitable_card(app_name, bitable_url, len(tasks))
                await feishu_client.send_chat_card(chat_id=chat_id, card=card)
                logger.info(f"多维表格链接已发送到群聊: {chat_id}")
            except Exception as e:
                logger.warning(f"发送群聊消息失败: {e}")

        logger.info(f"多维表格创建成功: {app_name} (app_token: {app_token})")
        return {
            "ok": True,
            "app_token": app_token,
            "table_id": result.get("table_id", ""),
            "url": bitable_url,
            "app_name": app_name,
            "records_written": len(tasks),
            "message": f"多维表格创建成功，已写入{len(tasks)}条任务记录",
        }

    except Exception as e:
        logger.error(f"创建多维表格失败: {e}")
        return {"ok": False, "error": str(e), "app_name": app_name}


# ==================== Step 4: 发布群公告 ====================

async def _execute_step4_announcement(
    plan: ExecutionPlan,
    goal_name: str,
    tasks: list[dict],
    chat_id: str,
    user_name: str = "",
) -> dict:
    """AI生成活动草案并发布到群公告"""
    if not chat_id:
        return {"ok": False, "error": "群聊未创建，无法发布公告"}

    try:
        # AI生成活动草案
        tasks_text = json_mod.dumps(
            [{"name": t.get("name"), "priority": t.get("priority")} for t in tasks],
            ensure_ascii=False,
        )
        prompt = ACTIVITY_DRAFT_PROMPT.format(
            goal=plan.goal,
            goal_name=goal_name,
            tasks=tasks_text,
            user_name=user_name or "当前用户",
        )
        ai_response = await llm_engine.generate(prompt)
        draft = parse_ai_json(ai_response)

        if not draft:
            return {"ok": False, "error": "AI无法生成活动草案"}

        content = draft.get("content", f"# {goal_name}活动策划书\n\n待补充详细内容")
        draft_title = draft.get("title", "")

        # 先尝试发布群公告
        result = await feishu_client.set_chat_announcement(
            chat_id=chat_id,
            content=content,
        )

        if result.get("code") == 0:
            logger.info(f"群公告发布成功: {chat_id}")
            return {
                "ok": True,
                "message": "活动草案已发布到群公告",
                "draft_title": draft_title,
                "draft_content": content[:200],
            }

        # 公告API失败（如docx类型群聊不支持），改用卡片消息发送
        logger.warning(f"群公告API失败，改用卡片消息发送: {result.get('msg', '')}")
        try:
            card = _build_announcement_card(draft_title or f"{goal_name}活动策划书", content)
            await feishu_client.send_chat_card(chat_id=chat_id, card=card)
            logger.info(f"活动草案已通过卡片消息发送到群聊: {chat_id}")
            return {
                "ok": True,
                "message": "活动草案已通过卡片消息发布到群聊",
                "draft_title": draft_title,
                "draft_content": content[:200],
            }
        except Exception as card_e:
            logger.warning(f"发送卡片消息也失败: {card_e}")
            return {
                "ok": False,
                "error": f"群公告发布失败: {result.get('msg', '')}",
                "draft_content": content,
            }

    except Exception as e:
        logger.error(f"发布群公告失败: {e}")
        return {"ok": False, "error": str(e)}


# ==================== Step 5: 创建问卷（选做） ====================

async def _execute_step5_create_survey(
    plan: ExecutionPlan,
    goal_name: str,
    tasks: list[dict],
    chat_id: str,
    user_access_token: str,
) -> dict:
    """AI分析问卷字段并创建问卷（多维表格实现）"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    app_name = f"{goal_name}报名问卷_{timestamp}"

    try:
        # AI分析问卷字段
        tasks_text = json_mod.dumps(
            [{"name": t.get("name"), "priority": t.get("priority")} for t in tasks],
            ensure_ascii=False,
        )
        prompt = SURVEY_FIELDS_PROMPT.format(
            goal=plan.goal,
            goal_name=goal_name,
            tasks=tasks_text,
        )
        ai_response = await llm_engine.generate(prompt)
        survey_data = parse_ai_json(ai_response)

        if not survey_data:
            return {"ok": False, "error": "AI无法生成问卷字段"}

        survey_name = survey_data.get("survey_name", f"{goal_name}问卷")
        survey_fields = survey_data.get("fields", [])

        # 转换为多维表格字段格式
        bitable_fields = []
        for i, field in enumerate(survey_fields):
            field_type = field.get("type", "text")
            if field_type == "text":
                bitable_type = 1
            elif field_type == "single_select":
                bitable_type = 3
            elif field_type == "multi_select":
                bitable_type = 4
            elif field_type == "date":
                bitable_type = 5
            else:
                bitable_type = 1

            field_def = {
                "field_name": field.get("name", f"字段{i+1}"),
                "type": bitable_type,
            }
            if "options" in field and field["options"]:
                field_def["property"] = {"options": [{"name": o, "color": (j % 20)} for j, o in enumerate(field["options"])]}
            bitable_fields.append(field_def)

        if not bitable_fields:
            bitable_fields = [{"field_name": "姓名", "type": 1}]

        # 创建问卷多维表格
        result = await feishu_client.create_bitable_with_template(
            app_name=app_name,
            table_name=survey_name,
            fields=bitable_fields,
            records=[],
            user_access_token=user_access_token,
        )

        if not result.get("ok"):
            return {"ok": False, "error": result.get("error", "创建问卷失败")}

        app_token = result["app_token"]
        table_id = result.get("table_id", "")
        survey_url = result["url"]
        plan.add_resource("survey", app_token, app_name)

        # 创建表单视图，让问卷看起来像真正的问卷
        form_view_url = survey_url
        if table_id:
            try:
                view_result = await feishu_client.bitable_create_form_view(
                    app_token=app_token,
                    table_id=table_id,
                    view_name=f"{survey_name}表单",
                    user_access_token=user_access_token,
                )
                if view_result.get("code") == 0:
                    view_id = view_result.get("data", {}).get("view", {}).get("view_id", "")
                    if view_id:
                        # 使用 /form/ 路径直接打开表单视图（而非表格视图）
                        form_view_url = f"https://{feishu_client.TENANT_DOMAIN}/base/{app_token}/form/{view_id}"
                        logger.info(f"表单视图创建成功: {form_view_url}")
                else:
                    logger.warning(f"创建表单视图失败: {view_result.get('msg', '')}")
            except Exception as e:
                logger.warning(f"创建表单视图异常: {e}")

        # 向群聊发送问卷链接
        if chat_id:
            try:
                card = _build_survey_card(survey_name, form_view_url, survey_fields)
                await feishu_client.send_chat_card(chat_id=chat_id, card=card)
            except Exception as e:
                logger.warning(f"发送问卷链接失败: {e}")

        logger.info(f"问卷创建成功: {app_name} (app_token: {app_token})")
        return {
            "ok": True,
            "app_token": app_token,
            "url": form_view_url,
            "survey_name": survey_name,
            "fields": survey_fields,
            "message": f"问卷创建成功: {survey_name}",
        }

    except Exception as e:
        logger.error(f"创建问卷失败: {e}")
        return {"ok": False, "error": str(e)}


# ==================== 辅助函数 ====================

def _build_bitable_card(app_name: str, url: str, record_count: int) -> dict:
    """构建多维表格链接卡片"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📊 进度表格已创建"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{app_name}**\n\n已写入 {record_count} 条任务记录，请点击下方按钮查看进度。",
                },
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开进度表格"},
                        "type": "primary",
                        "url": url,
                    }
                ],
            },
        ],
    }


def _build_survey_card(survey_name: str, url: str, fields: list[dict]) -> dict:
    """构建问卷链接卡片"""
    fields_text = "\n".join([f"- {f.get('name', '')} ({'必填' if f.get('required') else '选填'})" for f in fields])
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📋 报名问卷已创建"},
            "template": "green",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{survey_name}**\n\n问卷字段：\n{fields_text}\n\n请点击下方按钮填写问卷。",
                },
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "填写问卷"},
                        "type": "primary",
                        "url": url,
                    }
                ],
            },
        ],
    }


def _build_announcement_card(title: str, content: str) -> dict:
    """构建活动草案卡片消息（公告API失败时的降级方案）"""
    # 截取内容前500字符作为卡片摘要
    summary = content[:500] + ("..." if len(content) > 500 else "")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"📢 {title}"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": summary,
                },
            },
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": "此活动草案由AI自动生成，群公告API暂不支持当前群类型，已通过卡片消息发布。"}
                ],
            },
        ],
    }


def _build_failed_response(plan: ExecutionPlan, step_result: dict) -> dict:
    """构建失败响应，包含已创建资源列表"""
    return {
        "ok": False,
        "message": f"执行失败: {step_result.get('error', '未知错误')}",
        "execution_id": plan.execution_id,
        "status": "failed",
        "error": step_result.get("error", ""),
        "created_resources": plan.created_resources,
        "can_rollback": len(plan.created_resources) > 0,
    }


async def get_execution_status(execution_id: str) -> dict:
    """获取执行计划状态"""
    try:
        if execution_id not in _execution_plans:
            return {"ok": False, "error": "执行计划不存在"}

        plan = _execution_plans[execution_id]
        return {
            "ok": True,
            "execution_id": execution_id,
            "goal": plan.goal,
            "goal_name": plan.plan_data.get("goal_name", plan.goal),
            "status": plan.status,
            "status_text": EXECUTION_STATES.get(plan.status, plan.status),
            "current_step": plan.current_step,
            "created_at": plan.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "results": plan.results,
            "created_resources": plan.created_resources,
        }

    except Exception as e:
        logger.error(f"获取执行状态失败: {e}")
        return {"ok": False, "error": str(e)}


async def control_execution(execution_id: str, action: str) -> dict:
    """控制执行计划（暂停/继续/终止）"""
    try:
        if execution_id not in _execution_plans:
            return {"ok": False, "error": "执行计划不存在"}

        plan = _execution_plans[execution_id]

        if action == "pause":
            plan.pause()
        elif action == "resume":
            plan.resume()
        elif action == "cancel":
            plan.cancel()
        else:
            return {"ok": False, "error": "无效的操作类型，支持：pause/resume/cancel"}

        return {
            "ok": True,
            "message": f"执行计划已{EXECUTION_STATES.get(plan.status, plan.status)}",
            "execution_id": execution_id,
            "status": plan.status,
        }

    except Exception as e:
        logger.error(f"控制执行计划失败: {e}")
        return {"ok": False, "error": str(e)}