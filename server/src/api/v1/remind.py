"""柔性催办 API 路由

POST   /api/v1/remind/natural      - 自然语言催办（AI解析）
GET    /api/v1/remind/overdue      - 查询逾期任务列表
POST   /api/v1/remind/action       - 处理卡片回调动作
POST   /api/v1/remind/delay        - 处理延期申请提交
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from server.src.services.remind_service import (
    handle_card_action,
    get_overdue_tasks,
    remind_by_natural_language,
    handle_delay_submit,
)
from server.src.core.auth import require_member

router = APIRouter(prefix="/remind", tags=["remind"])


class NaturalRemindRequest(BaseModel):
    user_input: str
    user_access_token: str = ""


@router.post("/natural")
async def remind_natural(
    req: NaturalRemindRequest,
    user=Depends(require_member),
):
    """自然语言催办（AI解析）

    Args:
        user_input: 自然语言输入，如"催办绿大萌的作业批改任务"
        user_access_token: 用户访问令牌
    """
    return await remind_by_natural_language(
        user_input=req.user_input,
        user_access_token=req.user_access_token,
    )


@router.get("/overdue")
async def remind_overdue(
    user_access_token: str = "",
    user=Depends(require_member),
):
    """查询逾期任务列表

    Args:
        user_access_token: 用户访问令牌（必须使用用户身份）

    Returns:
        逾期任务列表，按逾期天数排序
    """
    return await get_overdue_tasks(user_access_token=user_access_token)


class CardActionRequest(BaseModel):
    action_data: dict
    user_access_token: str = ""


@router.post("/action")
async def remind_action(
    req: CardActionRequest,
    user=Depends(require_member),
):
    """处理卡片回调动作

    Args:
        action_data: 卡片动作数据（包含task_id, action, assignee_id, assignee_name, initiator_id）
        user_access_token: 用户访问令牌
    """
    return await handle_card_action(req.action_data, req.user_access_token)


class DelaySubmitRequest(BaseModel):
    action_data: dict
    form_data: dict
    user_access_token: str = ""


@router.post("/delay")
async def remind_delay(
    req: DelaySubmitRequest,
    user=Depends(require_member),
):
    """处理延期申请提交

    Args:
        action_data: 卡片动作数据
        form_data: 表单数据（delay_reason, new_due_date, delay_note）
        user_access_token: 用户访问令牌
    """
    return await handle_delay_submit(
        action_data=req.action_data,
        form_data=req.form_data,
        user_access_token=req.user_access_token,
    )
