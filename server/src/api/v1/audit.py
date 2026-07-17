"""审批处理 API 路由

POST /api/v1/audit/{instance_id}      - 处理审批（通过/拒绝）
GET  /api/v1/audit/tasks              - 获取审批任务列表
"""

from fastapi import APIRouter, Path, Query, Depends
from pydantic import BaseModel

from server.src.services.audit_service import (
    handle_approval,
    list_approval_tasks,
)
from server.src.core.auth import require_admin

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditHandleRequest(BaseModel):
    action_type: str
    comment: str = ""
    applicant_id: str | None = None
    user_access_token: str = ""
    user_id: str = ""
    task_id: str = ""  # 可选：直接指定任务ID，跳过实例查询


@router.get("/tasks")
async def audit_tasks(
    topic: int = Query(1, description="任务分组，1=待审批，2=已审批，3=我发起的"),
    definition_code: str = Query("", description="审批定义码过滤"),
    page_size: int = Query(20, description="每页数量"),
    user_access_token: str = Query("", description="用户访问令牌"),
    user=Depends(require_admin),
):
    """获取审批任务列表"""
    return await list_approval_tasks(
        topic=topic,
        definition_code=definition_code,
        page_size=page_size,
        user_access_token=user_access_token,
    )


@router.post("/{instance_id}")
async def audit_handle(
    instance_id: str = Path(..., description="审批实例ID"),
    req: AuditHandleRequest = None,
    user=Depends(require_admin),
):
    """处理审批（通过/拒绝）

    Args:
        instance_id: 审批实例ID
        action_type: pass/reject
        comment: 审批意见
        applicant_id: 申请人ID（用于通知）
        user_access_token: 用户访问令牌
    """
    if req is None:
        return {"ok": False, "error": "缺少请求体"}

    return await handle_approval(
        instance_id=instance_id,
        action_type=req.action_type,
        comment=req.comment,
        applicant_id=req.applicant_id,
        user_access_token=req.user_access_token,
        user_id=req.user_id,
        task_id=req.task_id,
    )