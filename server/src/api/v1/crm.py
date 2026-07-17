"""CRM查询 API 路由

POST /api/v1/crm        - 查询客户信息
GET  /api/v1/crm/list   - 获取客户列表
"""

from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel

from server.src.services.crm_service import query_customer, list_customers
from server.src.core.auth import require_admin

router = APIRouter(prefix="/crm", tags=["crm"])


class CrmQueryRequest(BaseModel):
    customer_name: str
    chat_id: str | None = None
    user_access_token: str = ""


@router.post("")
async def crm_query(
    req: CrmQueryRequest,
    user=Depends(require_admin),
):
    """查询客户信息

    Args:
        customer_name: 客户名称
        chat_id: 群聊ID（用于推送信息）
        user_access_token: 用户访问令牌
    """
    return await query_customer(
        customer_name=req.customer_name,
        chat_id=req.chat_id,
        user_access_token=req.user_access_token,
    )


@router.get("/list")
async def crm_list(
    status: str | None = Query(None, description="状态过滤"),
    limit: int = Query(10, description="返回数量"),
    user_access_token: str = Query("", description="用户访问令牌"),
    user=Depends(require_admin),
):
    """获取客户列表"""
    return await list_customers(
        status=status,
        limit=limit,
        user_access_token=user_access_token,
    )
