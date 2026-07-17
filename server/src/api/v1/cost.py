"""成本录入 API 路由

POST /api/v1/cost - 录入成本并触发审批
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from server.src.services.cost_service import create_cost_record
from server.src.core.auth import require_admin

router = APIRouter(prefix="/cost", tags=["cost"])


class CostCreateRequest(BaseModel):
    project_name: str
    amount: float
    category: str
    description: str = ""
    approval_code: str | None = None
    open_id: str = ""
    title: str = ""
    user_access_token: str = ""


@router.post("")
async def cost_create(
    req: CostCreateRequest,
    user=Depends(require_admin),
):
    """录入成本并触发飞书审批

    Args:
        project_name: 项目名称
        amount: 金额
        category: 分类（如：人力成本、物料成本、差旅费用等）
        description: 费用描述（可选）
        approval_code: 审批定义码（可选，未提供时自动搜索）
        open_id: 审批发起人open_id（应用身份时需要）
        title: 审批实例展示名称（可选）
        user_access_token: 用户访问令牌
    """
    return await create_cost_record(
        project_name=req.project_name,
        amount=req.amount,
        category=req.category,
        description=req.description,
        approval_code=req.approval_code,
        open_id=req.open_id,
        title=req.title,
        user_access_token=req.user_access_token,
    )