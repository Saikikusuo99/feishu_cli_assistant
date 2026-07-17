"""审批处理服务

处理 /audit 指令的业务逻辑：
- 支持通过/拒绝审批
- 填写审批理由
- 通知申请人审批结果
- 获取待审批任务列表
"""

import logging

from server.src.feishu.client import feishu_client

logger = logging.getLogger("lanshan-server.audit_service")


async def handle_approval(
    instance_id: str,
    action_type: str,
    comment: str = "",
    applicant_id: str | None = None,
    user_access_token: str = "",
    user_id: str = "",
    task_id: str = "",
) -> dict:
    """处理审批（通过/拒绝）

    流程：
    1. 如果提供了 task_id，直接使用（跳过实例查询）
    2. 否则获取实例详情，找到第一个待审批任务（PENDING）
    3. 使用 tasks/approve 接口审批该任务

    Args:
        instance_id: 审批实例ID
        action_type: pass/reject
        comment: 审批意见
        applicant_id: 申请人ID（用于通知）
        user_access_token: 用户访问令牌
        task_id: 可选，直接指定任务ID
    """
    try:
        if action_type not in ("pass", "reject"):
            return {"ok": False, "error": "action_type必须为pass或reject"}

        if not feishu_client.is_configured:
            return {"ok": False, "error": "飞书未配置，请先完成飞书应用配置"}

        approval_code = ""

        if task_id:
            # 用户提供了 task_id，跳过实例查询，直接审批
            # 但仍需获取 approval_code（飞书审批操作需要）
            instance_detail = await feishu_client.approval_get_instance(
                instance_id=instance_id,
                user_access_token=user_access_token,
            )
            if not instance_detail.get("error"):
                instance_data = instance_detail.get("data", {})
                approval_code = instance_data.get("approval_code", "")
        else:
            # 常规流程：获取实例详情，从中提取 task_id
            instance_detail = await feishu_client.approval_get_instance(
                instance_id=instance_id,
                user_access_token=user_access_token,
            )

            if instance_detail.get("error"):
                logger.error(f"获取实例详情失败: {instance_detail.get('msg')}")
                return {"ok": False, "error": f"获取实例详情失败: {instance_detail.get('msg', '未知错误')}"}

            instance_data = instance_detail.get("data", {})
            task_list = instance_data.get("task_list", [])

            if not task_list:
                logger.warning("实例中没有找到审批任务")
                return {"ok": False, "error": "实例中没有审批任务"}

            pending_task = None
            for task in task_list:
                if task.get("status") == "PENDING":
                    pending_task = task
                    break

            if not pending_task:
                logger.warning("实例中没有待审批任务")
                return {"ok": False, "error": "实例中没有待审批任务，可能已被处理"}

            task_id = pending_task.get("id", "")
            approval_code = instance_data.get("approval_code", "")

        # 使用 approval_approve_task 方法（内部已处理路径区分和回退逻辑）
        result = await feishu_client.approval_approve_task(
            task_id=task_id,
            instance_code=instance_id,
            action_type=action_type,
            comment=comment,
            approval_code=approval_code,
            user_id=user_id,
            user_access_token=user_access_token,
        )

        if result.get("error"):
            logger.error(f"审批操作失败: {result.get('msg')}")
            return {"ok": False, "error": f"审批操作失败: {result.get('msg', '未知错误')}"}

        if applicant_id:
            action_text = "通过" if action_type == "pass" else "拒绝"
            notify_result = await feishu_client.send_message(
                receive_id=applicant_id,
                content=f'{{"text":"您的审批申请已{action_text}\\n审批实例ID：{instance_id}\\n审批意见：{comment}"}}',
                msg_type="text",
                use_app_token=True,
            )
            if notify_result.get("error"):
                logger.warning("通知申请人失败")

        action_text = "通过" if action_type == "pass" else "拒绝"
        return {
            "ok": True,
            "message": f"审批已{action_text}",
            "instance_id": instance_id,
            "action_type": action_type,
            "comment": comment,
        }

    except Exception as e:
        logger.error(f"处理审批失败: {e}")
        return {"ok": False, "error": str(e)}


async def list_approval_tasks(
    topic: int = 1,
    definition_code: str = "",
    page_size: int = 20,
    user_access_token: str = "",
) -> dict:
    """获取审批任务列表

    Args:
        topic: 任务分组，1=待审批，2=已审批，3=我发起的
        definition_code: 审批定义码过滤
        page_size: 每页数量
    """
    try:
        if not feishu_client.is_configured:
            return {"ok": False, "error": "飞书未配置，请先完成飞书应用配置"}

        result = await feishu_client.approval_get_tasks(
            topic=topic,
            definition_code=definition_code,
            page_size=page_size,
            user_access_token=user_access_token,
        )

        if result.get("error"):
            logger.error(f"获取审批任务列表失败: {result.get('msg')}")
            return {"ok": False, "error": f"获取审批任务列表失败: {result.get('msg', '未知错误')}"}

        tasks = result.get("data", {}).get("tasks", [])
        return {
            "ok": True,
            "message": f"获取到 {len(tasks)} 条审批任务",
            "tasks": tasks,
        }

    except Exception as e:
        logger.error(f"获取审批任务列表失败: {e}")
        return {"ok": False, "error": str(e)}