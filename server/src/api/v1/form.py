"""表单分析 API 路由

POST /api/v1/form/analyze - 分析表单数据
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from server.src.services.form_service import analyze_form
from server.src.core.auth import require_admin

router = APIRouter(prefix="/form", tags=["form"])


class FormAnalyzeRequest(BaseModel):
    target: str
    user_access_token: str = ""
    send_to_user: str = ""


@router.post("/analyze")
async def form_analyze(
    req: FormAnalyzeRequest,
    user=Depends(require_admin),
):
    """分析表单数据

    Args:
        target: 多维表格目标（飞书URL 或 项目名）
        user_access_token: 用户访问令牌
        send_to_user: 接收报告的用户open_id（为空则不发送）
    """
    return await analyze_form(
        target=req.target,
        user_access_token=req.user_access_token,
        send_to_user=req.send_to_user,
    )
